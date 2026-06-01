import unittest
from tests.test_base.test_project.src.models.user import User

class TestUser(unittest.TestCase):
    
    def test_user_creation(self):
        user = User("Test", "test@example.com", 25)
        self.assertEqual(user.name, "Test")
        
    def test_is_adult(self):
        adult = User("Adult", "adult@example.com", 25)
        child = User("Child", "child@example.com", 15)
        self.assertTrue(adult.is_adult())  
        self.assertFalse(child.is_adult())
