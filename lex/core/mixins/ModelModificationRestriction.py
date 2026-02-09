from abc import ABC


class ModelModificationRestriction(ABC):
    """
    In order to restrict updating, deleting or creating instances of a certain model M, a class (say, X) inheriting
    this class @ModelModificationRestriction has to be defined and the desired methods have to be overwritten
    (further down in these docs it is explained which methods should be overwritten depending on your needs).
    Then, an instance of class X has to be added to the desired model A as class attribute 'modification_restriction'.

    The modification restriction works as follows:
    When a user tries to update or delete an instance of the model M, it is first checked whether the user is allowed
    to do this in general by calling the method @can_modify_in_general (default: True). If this results to False,
    an error is raised. Otherwise it is checked by calling the method @can_be_modified, if the user is allowed to
    modify the certain instance specifically. Again, if this results to False, an error is raised, otherwise
    the change is performed. The mechanism works similarly for creating an instance or reading instances.
    The parameter violations of the four methods is an array, where you can add error messages that explain
    why the user cannot perform the database change. These error messages are then shown in the raised error.
    """

    def can_create_in_general(self, user, violations):
        """
        determines whether the given user is allowed to create instances of the model in general
        """
        return True

    def can_read_in_general(self, user, violations):
        """
        determines whether the given user is allowed to read instances of the model in general. If this results in
        false for a certain user, the user will not see the existence of this model at all
        """
        return True

    def can_modify_in_general(self, user, violations):
        """
        determines whether the given user is allowed to update or delete instances of the model in general.
        """
        return True
    def can_delete_in_general(self, user, violations):
        """
        determines whether the given user can modify the given instance.
        Important: this method is called on the 'old' instance (i.e. before the modification)!
        """
        return True
    def can_be_read(self, instance, user, violations):
        """
        determines whether the given user can read the given instance
        """
        return True

    def can_be_modified(self, instance, user, violations, request_data):
        """
        determines whether the given user can modify the given instance.
        Important: this method is called on the 'old' instance (i.e. before the modification)!
        """
        return True

    def can_be_deleted(self, instance, user, violations):
        """
        determines whether the given user can modify the given instance.
        Important: this method is called on the 'old' instance (i.e. before the modification)!
        """
        return True


class AdminReportsModificationRestriction(ModelModificationRestriction):

    def can_read_in_general(self, user, violations):
        return True

    def can_modify_in_general(self, user, violations):
        return False

    def can_create_in_general(self, user, violations):
        return False

    def can_delete_in_general(self, user, violations):
        return False

    def can_be_read(self, instance, user, violations):
        return True

    def can_be_modified(self, instance, user, violations):
        return False

    def can_be_created(self, instance, user, violations):
        return False

    def can_be_deleted(self, instance, user, violations):
        return False


class ExampleModelModificationRestriction(ModelModificationRestriction):

    def can_read_in_general(self, user, violations):
        pass

    def can_modify_in_general(self, user, violations):
        pass

    def can_create_in_general(self, user, violations):
        pass

    def can_be_read(self, instance, user, violations):
        pass

    def can_be_modified(self, instance, user, violations):
        pass

    def can_be_created(self, instance, user, violations):
        pass