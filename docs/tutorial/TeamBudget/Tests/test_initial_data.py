from lex.lex_app.tests.ProcessAdminTestCase import ProcessAdminTestCase

from Input.Team import Team
from Input.Employee import Employee
from Input.Expense import Expense


class InitialDataTest(ProcessAdminTestCase):
    """Load the TeamBudget sample data via test_data.json."""

    data_loaded = False

    def setUp(self):
        if not InitialDataTest.data_loaded:
            super().setUp()
            InitialDataTest.data_loaded = True

    def test_teams_created(self):
        self.assertEqual(Team.objects.count(), 3)

    def test_employees_created(self):
        self.assertEqual(Employee.objects.count(), 9)

    def test_expenses_created(self):
        self.assertEqual(Expense.objects.count(), 14)

    def test_foreign_keys_resolved(self):
        anna = Employee.objects.get(email="anna.schmidt@apex-consulting.com")
        self.assertEqual(anna.team.name, "Design")
