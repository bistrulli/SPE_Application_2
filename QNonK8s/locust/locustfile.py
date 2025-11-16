import os
import sys
from locust import HttpUser, task, constant
import numpy as np
import time
import logging
import gevent

# Import Prometheus exporter for metrics
import locust_prometheus_exporter

# --- Mandatory Think Time from Environment Variable ---
# This approach is robust because it doesn't conflict with Locust's argument parser.
def get_think_time_from_env():
    """
    Reads the think time from a mandatory environment variable.
    Exits with an error if the variable is not set or is invalid.
    """
    think_time_str = os.environ.get("LOCUST_THINK_TIME")
    
    if think_time_str is None:
        print("❌ ERROR: Mandatory environment variable LOCUST_THINK_TIME is not set.")
        print("   Please set it before running Locust, for example: export LOCUST_THINK_TIME=0.42")
        sys.exit(1)
        
    try:
        value = float(think_time_str)
        return value
    except ValueError:
        print(f"❌ ERROR: Invalid value for LOCUST_THINK_TIME: '{think_time_str}'. Must be a number.")
        sys.exit(1)

# Get the think time at the start of the script.
THINK_TIME = get_think_time_from_env()

print(f"✅ User think time configured to a constant: {THINK_TIME} seconds.")

# Configure logging to stdout so messages appear in headless runs as well
logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True)
logger = logging.getLogger(__name__)


class SimpleUser(HttpUser):
    """
    Defines the behavior of a single user.
    The wait_time is configured via the mandatory LOCUST_THINK_TIME environment variable.
    """
    # Set the wait_time directly as a class attribute.
    #wait_time = constant(THINK_TIME)

    @task
    def access_entrypoint(self):
        """
        This task defines the action the user performs: making a GET request.
        """
        think_time = np.random.exponential(THINK_TIME) # in s
        logger.debug(f"Think time (planned): {think_time} seconds")
        st = time.time()
        gevent.sleep(think_time)
        end = time.time()
        logger.debug(f"Think time (actual): {end - st} seconds")
        self.client.get("/")