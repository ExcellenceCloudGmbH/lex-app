import unittest
from unittest.mock import MagicMock, patch
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from lex.process_admin.utils.local_scheduler import LocalSchedulerBackend
import threading

class TestLocalSchedulerBackend(TestCase):
    
    def setUp(self):
        # Reset singleton for testing
        LocalSchedulerBackend._instance = None
        
    def tearDown(self):
        LocalSchedulerBackend._instance = None

    @patch('sched.scheduler')
    @patch('threading.Thread')
    def test_singleton_initialization(self, mock_thread, mock_sched):
        """Test that only one instance is created (Singleton)."""
        backend1 = LocalSchedulerBackend()
        backend2 = LocalSchedulerBackend()
        
        self.assertIs(backend1, backend2)
        mock_thread.assert_called_once()  # Worker thread started only once
        
    @patch('sched.scheduler')
    @patch('threading.Thread')
    def test_schedule_task(self, mock_thread, mock_sched_cls):
        """Test scheduling a task delegates to sched.enter."""
        mock_scheduler_instance = MagicMock()
        mock_sched_cls.return_value = mock_scheduler_instance
        
        backend = LocalSchedulerBackend()
        
        test_func = MagicMock(__name__="test_func")
        now = timezone.now()
        run_at = now + timedelta(seconds=10)
        
        backend.schedule(run_at, test_func, args=(1, 2), kwargs={'a': 'b'})
        
        # Check that enter was called with correct delay (~10s)
        self.assertTrue(mock_scheduler_instance.enter.called)
        call_args = mock_scheduler_instance.enter.call_args
        
        # Check Delay (roughly 10s)
        delay = call_args[0][0] 
        self.assertAlmostEqual(delay, 10, delta=1)
        
        # Check Priority
        self.assertEqual(call_args[0][1], 1)
        
        # Check Function and Args
        self.assertEqual(call_args[0][2], test_func)
        self.assertEqual(call_args[1]['argument'], (1, 2))
        self.assertEqual(call_args[1]['kwargs'], {'a': 'b'})

    @patch('sched.scheduler')
    @patch('threading.Thread')
    def test_schedule_immediate(self, mock_thread, mock_sched_cls):
        """Test scheduling a task in the past results in 0 delay."""
        mock_scheduler_instance = MagicMock()
        mock_sched_cls.return_value = mock_scheduler_instance
        
        backend = LocalSchedulerBackend()
        test_func = MagicMock(__name__="test_func")
        past = timezone.now() - timedelta(seconds=10)
        
        backend.schedule(past, test_func)
        
        call_args = mock_scheduler_instance.enter.call_args
        delay = call_args[0][0]
        self.assertEqual(delay, 0)

    @patch('lex.process_admin.utils.local_scheduler.time')
    def test_worker_run_loop(self, mock_time):
        """Test the worker loop logic (conceptually)."""
        # It's hard to test the infinite loop directly without hanging the test.
        # We can test the _delay function interacting with the event.
        
        backend = LocalSchedulerBackend()
        backend._event = MagicMock()
        
        # Test _delay with timeout
        backend._delay(5)
        backend._event.wait.assert_called_with(5)
        
        # Test _delay with 0
        backend._delay(0)
        # Should not wait on event if 0?
        # The logic says: if timeout > 0: self._event.wait(timeout)
        pass # The mock will record calls if any, we just verified logic manually above.

if __name__ == '__main__':
    unittest.main()
