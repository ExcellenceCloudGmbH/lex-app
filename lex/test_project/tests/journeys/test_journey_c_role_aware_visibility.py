"""
Journey C — "Role-aware field visibility"

Three callers — superuser, HR, regular staff — hit the same
:class:`Employee` record and must see *different* shapes of the same
truth. The journey exercises:

    Cluster 4 (Permissions)      — permission_read / permission_edit
    Cluster 4a (Field-level)     — allow_all / allow_all_except / allow_fields
    Cluster 10 (API layer)       — list + detail endpoints honouring authz
    Seam: serializer + view chain must carry the PermissionResult
          from the model all the way to the HTTP body.

Why this test exists
--------------------
Field-level permissions are the single most privacy-sensitive contract
in the framework. Missing a filter in the list path but catching it
in the detail path (or vice versa) is a customer-visible leak. This
journey asserts the same record serializes to three different shapes
across both endpoints.
"""

from __future__ import annotations

import unittest

from rest_framework import status

from lex.tests.e2e._authenticated_e2e_test_case import AuthenticatedE2ETestCase

from .models import ALL_MODELS, EMPLOYEE, Employee


class TestJourneyC_EmployeeVisibility_Superuser(AuthenticatedE2ETestCase):
    """Superuser sees every field on list + detail."""

    e2e_models = ALL_MODELS
    as_superuser = True

    def test_superuser_sees_every_field(self) -> None:
        Employee.objects.create(name="Ada", salary=100_000, ssn="111-22-3333")

        with self.subTest(act="1-list"):
            resp = self.client.get(self.url_list(EMPLOYEE))
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            rows = self.extract_results(resp.data)
            self.assertTrue(rows)
            for field in ("name", "salary", "ssn"):
                self.assertIn(
                    field, rows[0],
                    f"Superuser list response must include {field!r}",
                )

        with self.subTest(act="2-detail"):
            emp = Employee.objects.get(name="Ada")
            resp = self.client.get(self.url_detail(EMPLOYEE, emp.pk))
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            for field in ("name", "salary", "ssn"):
                self.assertIn(
                    field, resp.data,
                    f"Superuser detail response must include {field!r}",
                )


class TestJourneyC_EmployeeVisibility_HR(AuthenticatedE2ETestCase):
    """HR sees salary but not SSN on list + detail."""

    e2e_models = ALL_MODELS
    extra_groups = frozenset({"hr"})

    def test_hr_sees_salary_but_not_ssn(self) -> None:
        Employee.objects.create(name="Bob", salary=80_000, ssn="444-55-6666")

        with self.subTest(act="1-list"):
            resp = self.client.get(self.url_list(EMPLOYEE))
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            rows = self.extract_results(resp.data)
            self.assertTrue(rows)
            row = rows[0]
            self.assertIn("salary", row, "HR must see salary on list")
            self.assertNotIn(
                "ssn", row,
                "HR must NOT see ssn on list (allow_all_except)",
            )

        with self.subTest(act="2-detail"):
            emp = Employee.objects.get(name="Bob")
            resp = self.client.get(self.url_detail(EMPLOYEE, emp.pk))
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertIn("salary", resp.data, "HR must see salary on detail")
            self.assertNotIn(
                "ssn", resp.data,
                "HR must NOT see ssn on detail (same contract as list)",
            )


class TestJourneyC_EmployeeVisibility_Regular(AuthenticatedE2ETestCase):
    """Regular staff see only ``id`` + ``name`` on list + detail."""

    e2e_models = ALL_MODELS

    def test_regular_staff_see_only_public_fields(self) -> None:
        Employee.objects.create(name="Carol", salary=50_000, ssn="777-88-9999")

        with self.subTest(act="1-list"):
            resp = self.client.get(self.url_list(EMPLOYEE))
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            rows = self.extract_results(resp.data)
            self.assertTrue(rows)
            row = rows[0]
            self.assertIn("name", row, "Regular staff must see name")
            self.assertNotIn(
                "salary", row,
                "Regular staff must NOT see salary on list",
            )
            self.assertNotIn(
                "ssn", row,
                "Regular staff must NOT see ssn on list",
            )

        with self.subTest(act="2-detail"):
            emp = Employee.objects.get(name="Carol")
            resp = self.client.get(self.url_detail(EMPLOYEE, emp.pk))
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertIn("name", resp.data)
            self.assertNotIn(
                "salary", resp.data,
                "Field visibility must be consistent between list and detail",
            )
            self.assertNotIn("ssn", resp.data)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

