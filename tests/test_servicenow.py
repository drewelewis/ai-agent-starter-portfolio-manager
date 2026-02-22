"""
Test file for ServiceNow operations.
Before running these tests, ensure you have the following environment variables set:
- SERVICENOW_INSTANCE
- SERVICENOW_USERNAME and SERVICENOW_PASSWORD (or SERVICENOW_API_TOKEN)
"""

import os
from dotenv import load_dotenv
from operations.servicenow_operations import ServiceNowOperations

load_dotenv(override=True)


def test_create_case():
    """Test creating a new case."""
    print("\n=== Testing Case Creation ===")
    operations = ServiceNowOperations()
    
    result = operations.create_case(
        short_description="Test case from Python API",
        description="This is a test case created via the ServiceNow REST API.",
        priority="3"
    )
    
    print(f"Result: {result}")
    
    if result.get("success"):
        return result.get("sys_id")
    return None


def test_get_case(case_sys_id):
    """Test retrieving a case by sys_id."""
    print("\n=== Testing Get Case by Sys ID ===")
    operations = ServiceNowOperations()
    
    result = operations.get_case(case_sys_id)
    print(f"Result: {result}")


def test_get_case_by_number(case_number):
    """Test retrieving a case by case number."""
    print("\n=== Testing Get Case by Number ===")
    operations = ServiceNowOperations()
    
    result = operations.get_case_by_number(case_number)
    print(f"Result: {result}")


def test_update_case(case_sys_id):
    """Test updating a case."""
    print("\n=== Testing Update Case ===")
    operations = ServiceNowOperations()
    
    result = operations.update_case(
        case_sys_id=case_sys_id,
        updates={
            "priority": "2",
            "description": "Updated description via API test"
        }
    )
    
    print(f"Result: {result}")


def test_query_open_cases():
    """Test querying open cases."""
    print("\n=== Testing Query Open Cases ===")
    operations = ServiceNowOperations()
    
    result = operations.get_open_cases(limit=5)
    print(f"Found {result.get('count')} open cases")
    
    if result.get("success"):
        for case in result.get("cases", [])[:3]:  # Print first 3
            print(f"  - {case.get('number')}: {case.get('short_description')}")


def test_query_high_priority():
    """Test querying high priority cases."""
    print("\n=== Testing Query High Priority Cases ===")
    operations = ServiceNowOperations()
    
    result = operations.get_high_priority_cases(limit=5)
    print(f"Found {result.get('count')} high priority cases")
    
    if result.get("success"):
        for case in result.get("cases", [])[:3]:  # Print first 3
            print(f"  - {case.get('number')}: {case.get('short_description')} (Priority: {case.get('priority')})")


def test_search_cases():
    """Test searching cases by text."""
    print("\n=== Testing Search Cases ===")
    operations = ServiceNowOperations()
    
    result = operations.search_cases_by_text(
        search_text="test",
        limit=5
    )
    
    print(f"Found {result.get('count')} cases matching 'test'")
    
    if result.get("success"):
        for case in result.get("cases", [])[:3]:  # Print first 3
            print(f"  - {case.get('number')}: {case.get('short_description')}")


def test_add_comment(case_sys_id):
    """Test adding a comment to a case."""
    print("\n=== Testing Add Comment ===")
    operations = ServiceNowOperations()
    
    result = operations.add_case_comment(
        case_sys_id=case_sys_id,
        comment="This is a test work note added via API",
        comment_type="work_notes"
    )
    
    print(f"Result: {result}")


def test_custom_query():
    """Test custom query with ServiceNow encoded query."""
    print("\n=== Testing Custom Query ===")
    operations = ServiceNowOperations()
    
    # Query for cases with priority 1 or 2 that are not closed
    result = operations.query_cases(
        query="priority<=2^state!=4",
        limit=5,
        order_by="priority"
    )
    
    print(f"Found {result.get('count')} cases with priority 1-2")
    
    if result.get("success"):
        for case in result.get("cases", [])[:3]:  # Print first 3
            print(f"  - {case.get('number')}: {case.get('short_description')} (Priority: {case.get('priority')})")


def run_all_tests():
    """Run all tests in sequence."""
    print("Starting ServiceNow Operations Tests...")
    print("=" * 60)
    
    # Test 1: Query existing cases
    test_query_open_cases()
    test_query_high_priority()
    test_search_cases()
    test_custom_query()
    
    # Test 2: Create a new case
    case_sys_id = test_create_case()
    
    if case_sys_id:
        # Test 3: Get the case we just created
        test_get_case(case_sys_id)
        
        # Test 4: Update the case
        test_update_case(case_sys_id)
        
        # Test 5: Add a comment
        test_add_comment(case_sys_id)
        
        # Test 6: Get the updated case
        test_get_case(case_sys_id)
        
        print(f"\n{'=' * 60}")
        print(f"Created case sys_id: {case_sys_id}")
        print("Note: Case was not closed to allow manual verification")
        print(f"{'=' * 60}")
    else:
        print("\nFailed to create case, skipping dependent tests")
    
    print("\nAll tests completed!")


if __name__ == "__main__":
    # Check if credentials are set
    if not os.getenv('SERVICENOW_INSTANCE'):
        print("Error: SERVICENOW_INSTANCE environment variable not set")
        print("\nRequired environment variables:")
        print("  - SERVICENOW_INSTANCE (e.g., 'dev12345.service-now.com')")
        print("  - SERVICENOW_USERNAME and SERVICENOW_PASSWORD")
        print("  - OR SERVICENOW_API_TOKEN")
        exit(1)
    
    # Run all tests
    run_all_tests()
