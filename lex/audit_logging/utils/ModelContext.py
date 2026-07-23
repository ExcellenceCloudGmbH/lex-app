import contextvars
from contextlib import contextmanager
from typing import Optional, Any, List, Dict


class LogHeading:
    """
    Object-less frame on the model context stack.

    Represents a table-of-contents style section heading in the calculation
    log tree: ``with model_logging_context("Data preparation"):`` groups all
    nested log output under a titled node without requiring a backing model
    instance. Plain Python object so it survives the deepcopy used when the
    model context is shipped to Celery workers.
    """

    def __init__(self, title: str):
        self.title = title
        # pk of the persisted CalculationLog row for this frame — cached by
        # CalculationLog._persist_message after lazy creation so repeated log
        # calls inside the block don't re-run the get_or_create lookup.
        self.node_id: Optional[int] = None

    def __repr__(self) -> str:
        return f"LogHeading({self.title!r})"


class ModelContext:
    """
    A class-based model context manager that correctly handles nested contexts.

    This class uses a list internally to function as a proper stack, allowing for
    unlimited nesting depth. It exposes `current` and `parent` properties
    to maintain a consistent API with dependent modules like ContextResolver.
    """

    def __init__(self, stack: Optional[List[Any]] = None):
        """Initializes the context stack."""
        self._stack: List[Any] = stack if stack is not None else []

    @property
    def current(self) -> Optional[Any]:
        """
        Gets the current model from the top of the stack (LIFO).

        Returns:
            The most recently added model instance, or None if the stack is empty.
        """
        return self._stack[-1] if self._stack else None

    def get_root(self) -> Optional[Any]:
        return self._stack[0] if self._stack else None

    def get_root_model(self) -> Optional[Any]:
        """First real model frame from the bottom of the stack, skipping LogHeading frames."""
        for frame in self._stack:
            if not isinstance(frame, LogHeading):
                return frame
        return None

    def get_current_model(self) -> Optional[Any]:
        """Nearest real model frame from the top of the stack, skipping LogHeading frames."""
        for frame in reversed(self._stack):
            if not isinstance(frame, LogHeading):
                return frame
        return None


    @property
    def parent(self) -> Optional[Any]:
        """
        Gets the parent model of the current one from the stack.

        Returns:
            The second-to-last model instance, or None if there is no parent.
        """
        return self._stack[-2] if len(self._stack) > 1 else None
    def pop(self):
        """
        pop an instance from the stack.

        Args:
            instance: The model instance to remove from the context.
        """
        if len(self._stack) != 0:
            return self._stack.pop(-1)
        return None

    def push(self, instance: Any):
        """
        Pushes a new instance onto the stack.

        Args:
            instance: The model instance to add to the context.
        """
        self._stack.append(instance)

    def __repr__(self) -> str:
        """Provides a string representation for debugging."""
        return f"ModelContextStack(current={self.current}, parent={self.parent}, depth={len(self._stack)})"


# The context variable now holds an instance of our stack class.
_model_context: contextvars.ContextVar[Dict[str, ModelContext]] = contextvars.ContextVar(
    'model_context',
    default={'model_context':ModelContext()},
)


# The corrected implementation
# @contextmanager
# def model_logging_context(instance: Any):
#     # 1. Get the previous context object.
#     previous_context = _model_context.get()
#
#     # 2. Create a NEW list by copying the old one.
#     new_stack_list = previous_context._stack.copy()
#
#     # 3. Create a completely NEW context object with the new list.
#     new_context = ModelContext(new_stack_list)
#     new_context.push(instance)
#
#     # 4. Tell contextvars to use this NEW object. It saves the old one.
#     token = _model_context.set(new_context)
#     try:
#         yield
#     finally:
#         # 5. Tell contextvars to restore the OLD object it saved.
#         _model_context.reset(token)

@contextmanager
def model_logging_context(instance: Any):
    """
    Pushes an instance onto the model context stack for the duration of the block.

    This context manager correctly handles arbitrary levels of nesting by creating
    a new context stack for the nested block, ensuring the full context hierarchy
    is always preserved.

    A plain string is accepted as a table-of-contents style section heading:
    ``with model_logging_context("Data preparation"):`` groups all log output
    inside the block under a titled, object-less node in the calculation log
    tree. Headings nest freely with model instances and with each other.
    """
    if isinstance(instance, str):
        instance = LogHeading(instance)
    elif (
        instance is not None
        and not isinstance(instance, LogHeading)
        and not hasattr(instance, '_meta')
    ):
        raise TypeError(f"Expected Django model instance or heading string, got {type(instance)}")
    context = _model_context.get()['model_context']
    context.push(instance)
    try:
        yield
    except Exception as e:
        # You can access the current instance for logging easily.
        current_model = _model_context.get()['model_context'].current
        print(f"Error in model context with instance {current_model}: {e}")
        raise
    finally:
        # Use the token to reset the context variable to its previous state.
        context.pop()
