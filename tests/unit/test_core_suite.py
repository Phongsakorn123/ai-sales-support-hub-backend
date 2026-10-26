"""Small, high-value backend regression suite used by default and in CI.

The broader historical suite remains available under tests/extended.
"""

import unittest

from tests.extended.test_ai_product_suggestions import SuggestionEndpointTests
from tests.extended.test_auth_api import AuthAPITests
from tests.extended.test_product_api import ProductAPITests


CORE_TESTS = (
    (AuthAPITests, "test_register_normalizes_username_and_email_hashes_password_and_returns_safe_profile"),
    (AuthAPITests, "test_duplicate_email_registration_returns_conflict"),
    (AuthAPITests, "test_login_returns_usable_bearer_token"),
    (AuthAPITests, "test_access_token_expires_after_sixty_minutes"),
    (AuthAPITests, "test_product_routes_require_a_bearer_token"),
    (ProductAPITests, "test_create_product_serializes_decimal_and_utc_timestamps"),
    (ProductAPITests, "test_patch_updates_only_supplied_fields_and_refreshes_timestamp"),
    (ProductAPITests, "test_delete_returns_empty_204_and_missing_product_returns_404"),
    (SuggestionEndpointTests, "test_successful_recommendation_uses_database_facts"),
    (SuggestionEndpointTests, "test_model_failures_return_labelled_catalog_fallback"),
)


def load_tests(loader, standard_tests, pattern):
    """Select exactly ten essential test cases for normal discovery."""
    suite = unittest.TestSuite()
    for test_case, method_name in CORE_TESTS:
        suite.addTest(test_case(method_name))
    return suite
