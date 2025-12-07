#!/usr/bin/env python3
"""
Temperature-Controlled Fan Manager for LED Matrix
Monitors CPU temperature and adjusts fan speed via PWM
"""

import RPi.GPIO as GPIO
import time
import logging
import os
import json
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class FanManager:
    """Manages PWM fan speed based on CPU temperature"""
    
    # Default configuration
    DEFAULT_CONFIG = {
        'enabled': True,
        'gpio_pin': 12,  # GPIO12 (Pin 32) - PWM capable
        'pwm_frequency': 25000,  # 25kHz for quiet operation
        'temp_thresholds': {
            'off': 45,      # Below 45°C: Fan OFF
            'low': 50,      # 50-60°C: Low speed (40%)
            'medium': 60,   # 60-70°C: Medium speed (70%)
            'high': 70,     # 70°C+: High speed (100%)
        },
        'fan_speeds': {
            'off': 0,       # 0% duty cycle
            'low': 40,      # 40% duty cycle
            'medium': 70,   # 70% duty cycle
            'high': 100,    # 100% duty cycle
        },
        'min_fan_speed': 30,  # Minimum speed when fan is on (prevents stall)
        'update_interval': 5,  # Check temperature every 5 seconds
        'hysteresis': 2,       # Temperature must change by 2°C to switch speed
    }
    
    def __init__(self, config_path='/home/ledpi/LEDMatrix/config/fan_config.json'):
        """Initialize fan manager with configuration"""
        self.config_path = Path(config_path)
        self.config = self.load_config()
        self.current_speed = 0
        self.current_temp = 0
        self.pwm = None
        self.running = False
        
        # Temperature tracking for hysteresis
        self.last_speed_change_temp = 0
        
    def load_config(self):
        """Load configuration from file or use defaults"""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    user_config = json.load(f)
                    # Merge with defaults
                    config = self.DEFAULT_CONFIG.copy()
                    config.update(user_config)
                    logger.info(f"Loaded fan config from {self.config_path}")
                    return config
            except Exception as e:
                logger.warning(f"Error loading config: {e}, using defaults")
        else:
            logger.info("No config file found, using defaults")
            # Create default config file
            self.save_config(self.DEFAULT_CONFIG)
        
        return self.DEFAULT_CONFIG.copy()
    
    def save_config(self, config=None):
        """Save configuration to file"""
        if config is None:
            config = self.config
        
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=4)
            logger.info(f"Saved fan config to {self.config_path}")
        except Exception as e:
            logger.error(f"Error saving config: {e}")
    
    def get_cpu_temperature(self):
        """Get CPU temperature in Celsius"""
        try:
            # Method 1: vcgencmd (most accurate for RPi)
            result = os.popen('vcgencmd measure_temp').readline()
            temp_str = result.replace("temp=", "").replace("'C\n", "")
            return float(temp_str)
        except:
            try:
                # Method 2: thermal zone (fallback)
                with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                    temp_millidegrees = int(f.read().strip())
                    return temp_millidegrees / 1000.0
            except Exception as e:
                logger.error(f"Error reading temperature: {e}")
                return 50.0  # Safe default
    
    def calculate_fan_speed(self, temp):
        """Calculate appropriate fan speed based on temperature"""
        thresholds = self.config['temp_thresholds']
        speeds = self.config['fan_speeds']
        hysteresis = self.config['hysteresis']
        
        # Apply hysteresis - only change speed if temp change is significant
        temp_change = abs(temp - self.last_speed_change_temp)
        if temp_change < hysteresis and self.current_speed > 0:
            # Not enough change, keep current speed
            return self.current_speed
        
        # Determine speed based on temperature
        if temp < thresholds['off']:
            speed = speeds['off']
        elif temp < thresholds['low']:
            speed = speeds['low']
        elif temp < thresholds['medium']:
            speed = speeds['medium']
        else:
            speed = speeds['high']
        
        # Apply minimum speed if fan should be on
        if speed > 0 and speed < self.config['min_fan_speed']:
            speed = self.config['min_fan_speed']
        
        # Record temp if speed is changing
        if speed != self.current_speed:
            self.last_speed_change_temp = temp
        
        return speed
    
    def set_fan_speed(self, speed):
        """Set fan speed (0-100%)"""
        if not self.pwm:
            logger.warning("PWM not initialized, cannot set fan speed")
            return
        
        try:
            # Clamp speed to 0-100
            speed = max(0, min(100, speed))
            
            # Set PWM duty cycle
            self.pwm.ChangeDutyCycle(speed)
            
            if speed != self.current_speed:
                logger.info(f"Fan speed changed: {self.current_speed}% → {speed}% (Temp: {self.current_temp:.1f}°C)")
                self.current_speed = speed
        except Exception as e:
            logger.error(f"Error setting fan speed: {e}")
    
    def initialize_gpio(self):
        """Initialize GPIO for fan control"""
        try:
            # Use BCM pin numbering
            GPIO.setmode(GPIO.BCM)
            
            # Suppress warnings about channels already in use
            GPIO.setwarnings(False)
            
            # Setup fan control pin
            pin = self.config['gpio_pin']
            GPIO.setup(pin, GPIO.OUT)
            
            # Initialize PWM
            freq = self.config['pwm_frequency']
            self.pwm = GPIO.PWM(pin, freq)
            self.pwm.start(0)  # Start with fan off
            
            logger.info(f"GPIO initialized: Pin {pin}, PWM frequency {freq}Hz")
            return True
        except Exception as e:
            logger.error(f"Error initializing GPIO: {e}")
            return False
    
    def cleanup(self):
        """Clean up GPIO resources"""
        try:
            if self.pwm:
                self.pwm.stop()
            GPIO.cleanup()
            logger.info("GPIO cleaned up")
        except Exception as e:
            logger.error(f"Error cleaning up GPIO: {e}")
    
    def run(self):
        """Main loop - monitor temperature and adjust fan"""
        if not self.config['enabled']:
            logger.info("Fan control is disabled in config")
            return
        
        if not self.initialize_gpio():
            logger.error("Failed to initialize GPIO, exiting")
            return
        
        logger.info("Fan manager started")
        logger.info(f"Temperature thresholds: {self.config['temp_thresholds']}")
        logger.info(f"Fan speeds: {self.config['fan_speeds']}")
        
        self.running = True
        
        try:
            while self.running:
                # Get current temperature
                self.current_temp = self.get_cpu_temperature()
                
                # Calculate desired fan speed
                desired_speed = self.calculate_fan_speed(self.current_temp)
                
                # Update fan if needed
                if desired_speed != self.current_speed:
                    self.set_fan_speed(desired_speed)
                
                # Wait before next check
                time.sleep(self.config['update_interval'])
                
        except KeyboardInterrupt:
            logger.info("Fan manager stopped by user")
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
        finally:
            self.cleanup()
    
    def stop(self):
        """Stop the fan manager"""
        self.running = False
    
    def get_status(self):
        """Get current fan status for monitoring"""
        return {
            'enabled': self.config['enabled'],
            'temperature': self.current_temp,
            'fan_speed': self.current_speed,
            'gpio_pin': self.config['gpio_pin'],
            'thresholds': self.config['temp_thresholds']
        }


def main():
    """Main entry point"""
    logger.info("Starting LED Matrix Fan Manager")
    
    # Create and run fan manager
    fan_manager = FanManager()
    
    try:
        fan_manager.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        fan_manager.cleanup()
        return 1
    
    return 0


if __name__ == '__main__':
    exit(main())
