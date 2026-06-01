import unittest
from tests.test_base.test_project.src.calculator import Calculator

class TestCalculator(unittest.TestCase):
    
    def test_add(self):
        self.assertEqual(Calculator.add(2, 3), 5)
        
    def test_multiply(self):
        self.assertEqual(Calculator.multiply(4, 5), 20)

if __name__ == "__main__":
    unittest.main()
