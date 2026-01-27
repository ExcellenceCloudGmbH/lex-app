# We need a model that has been registered.
# Let's check 'InvestorNAV' or any registered model.
import django
from django.apps import apps

def check_structure():
    # Find a model with meta history
    target_model = None
    for model in apps.get_models():
        if hasattr(model, 'history') and hasattr(model.history.model, 'meta_history'):
            target_model = model
            break
            
    if not target_model:
        print("No registered bitemporal model found.")
        return

    hist_model = target_model.history.model
    meta_model = hist_model.meta_history.model
    
    print(f"Checking Model: {target_model.__name__}")
    print(f"Historical Model: {hist_model.__name__}")
    print(f"Meta Model: {meta_model.__name__}")
    
    # Check fields on Meta Model
    fields = [f.name for f in meta_model._meta.get_fields()]
    print(f"Meta Fields: {fields}")
    
    has_valid_from = 'valid_from' in fields
    has_valid_to = 'valid_to' in fields
    has_sys_from = 'sys_from' in fields
    has_sys_to = 'sys_to' in fields
    
    print(f"Has valid_from: {has_valid_from}")
    print(f"Has valid_to: {has_valid_to}")
    print(f"Has sys_from: {has_sys_from}")
    print(f"Has sys_to: {has_sys_to}")
    
    if has_valid_from and has_valid_to:
        print("SUCCESS: Meta Model contains copies of Valid Time fields.")
    else:
        print("FAILURE: Meta Model MISSING Valid Time fields.")

if __name__ == "__main__":
    django.setup()
    check_structure()
