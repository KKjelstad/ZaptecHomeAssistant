#!/usr/bin/env python3
"""
Quick test script to verify poll boost functionality.
Simulates triggering the boost and verifies the polling interval changes.
"""

import time
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MockZaptecBridge:
    """Simplified bridge for testing boost logic"""
    def __init__(self):
        self.poll_interval_charging = 10
        self.poll_interval_idle = 120
        self.boost_duration = 300
        self.current_poll_interval = self.poll_interval_idle
        self.boost_active = False
        self.boost_end_time = None
    
    def _activate_poll_boost(self):
        """Activate fast polling for a limited duration"""
        self.boost_active = True
        self.boost_end_time = time.time() + self.boost_duration
        logger.info(f"✅ Poll boost activated for {self.boost_duration}s (fast polling at {self.poll_interval_charging}s interval)")
    
    def _check_poll_boost_expiry(self):
        """Check if poll boost has expired"""
        if self.boost_active:
            if time.time() >= self.boost_end_time:
                self.boost_active = False
                logger.info("⏱️  Poll boost expired, returning to normal dynamic polling")
                return True
        return False
    
    def update_poll_interval(self, any_charging=False):
        """Update poll interval based on state"""
        if self.boost_active:
            new_interval = self.poll_interval_charging
            mode = "boost"
        elif any_charging:
            new_interval = self.poll_interval_charging
            mode = "charging"
        else:
            new_interval = self.poll_interval_idle
            mode = "idle"
        
        if new_interval != self.current_poll_interval:
            self.current_poll_interval = new_interval
            logger.info(f"📊 Poll interval changed to {self.current_poll_interval}s ({mode} mode)")
        
        return mode

def main():
    logger.info("🧪 Testing Poll Boost Feature")
    logger.info("-" * 50)
    
    bridge = MockZaptecBridge()
    
    # Test 1: Initial state (idle)
    logger.info("\n🔬 Test 1: Initial idle state")
    mode = bridge.update_poll_interval(any_charging=False)
    assert bridge.current_poll_interval == 120, "Should be at idle interval"
    assert mode == "idle", "Should be in idle mode"
    logger.info("✅ Initial state correct: 120s idle polling")
    
    # Test 2: Activate boost
    logger.info("\n🔬 Test 2: Activate poll boost")
    bridge._activate_poll_boost()
    assert bridge.boost_active, "Boost should be active"
    mode = bridge.update_poll_interval(any_charging=False)
    assert bridge.current_poll_interval == 10, "Should switch to fast polling"
    assert mode == "boost", "Should be in boost mode"
    logger.info("✅ Boost activated: switched to 10s fast polling")
    
    # Test 3: Boost remains active
    logger.info("\n🔬 Test 3: Boost stays active during duration")
    time.sleep(1)
    bridge._check_poll_boost_expiry()
    assert bridge.boost_active, "Boost should still be active"
    mode = bridge.update_poll_interval(any_charging=False)
    assert mode == "boost", "Should still be in boost mode"
    logger.info("✅ Boost still active after 1 second")
    
    # Test 4: Simulate boost expiry
    logger.info("\n🔬 Test 4: Simulate boost expiry")
    bridge.boost_end_time = time.time() - 1  # Force expiry
    bridge._check_poll_boost_expiry()
    assert not bridge.boost_active, "Boost should be expired"
    mode = bridge.update_poll_interval(any_charging=False)
    assert bridge.current_poll_interval == 120, "Should return to idle polling"
    assert mode == "idle", "Should return to idle mode"
    logger.info("✅ Boost expired: returned to 120s idle polling")
    
    # Test 5: Boost during charging
    logger.info("\n🔬 Test 5: Boost while charging")
    bridge._activate_poll_boost()
    mode = bridge.update_poll_interval(any_charging=True)
    assert mode == "boost", "Boost should override charging mode"
    assert bridge.current_poll_interval == 10, "Should use fast polling"
    logger.info("✅ Boost overrides charging mode correctly")
    
    # Test 6: After boost expires during charging
    logger.info("\n🔬 Test 6: After boost expires while charging")
    bridge.boost_end_time = time.time() - 1
    bridge._check_poll_boost_expiry()
    mode = bridge.update_poll_interval(any_charging=True)
    assert mode == "charging", "Should switch to charging mode"
    assert bridge.current_poll_interval == 10, "Should stay at fast polling (charging)"
    logger.info("✅ After boost expires, stays fast during charging")
    
    # Test 7: Charging stops
    logger.info("\n🔬 Test 7: Charging stops")
    mode = bridge.update_poll_interval(any_charging=False)
    assert mode == "idle", "Should switch to idle mode"
    assert bridge.current_poll_interval == 120, "Should switch to slow polling"
    logger.info("✅ When charging stops, switches to 120s idle polling")
    
    logger.info("\n" + "=" * 50)
    logger.info("🎉 All tests passed!")
    logger.info("=" * 50)

if __name__ == "__main__":
    main()
