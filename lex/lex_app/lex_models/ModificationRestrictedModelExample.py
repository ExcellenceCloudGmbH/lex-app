from lex.lex_app.lex_models.ModelModificationRestriction import ModelModificationRestriction


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
