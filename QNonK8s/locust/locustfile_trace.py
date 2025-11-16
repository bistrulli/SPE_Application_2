import os
import sys
import pandas as pd
from locust import HttpUser, task, constant, LoadTestShape

# Import Prometheus exporter for metrics
import locust_prometheus_exporter

# --- Helper function to read environment variables ---
def get_env_var(var_name, is_numeric=False, default=None):
    """
    Reads an environment variable. If it's mandatory (no default), exits if not set.
    """
    value = os.environ.get(var_name)
    if value is None:
        if default is not None:
            return default
        print(f"❌ ERROR: Mandatory environment variable '{var_name}' is not set.")
        sys.exit(1)
    
    if is_numeric:
        try:
            return float(value)
        except ValueError:
            print(f"❌ ERROR: Environment variable '{var_name}' must be a number, but got '{value}'.")
            sys.exit(1)
    return value

# --- TraceShape Class ---
class TraceShape(LoadTestShape):
    
    time_limit = int(get_env_var("LOCUST_TIME_LIMIT", is_numeric=True))
    trace_file = get_env_var("LOCUST_WORKLOAD_CSV")
    
    # CORRECTED: Read amplitude and shift from environment variables
    amplitude = int(get_env_var("LOCUST_AMPLITUDE", is_numeric=True, default=50))
    shift = int(get_env_var("LOCUST_SHIFT", is_numeric=True, default=10))
    
    fit_trace = get_env_var("LOCUST_FIT_TRACE", default="false").lower() == "true"
    
    def __init__(self):
        super().__init__()
        try:
            trace_data = pd.read_csv(self.trace_file).to_numpy().T[0]
            self.max_index = len(trace_data)
            max_val = max(trace_data)
            min_val = min(trace_data)
            self.scaled_trace = [
                int((v - min_val) / (max_val - min_val) * self.amplitude + self.shift)
                for v in trace_data
            ]
            print(f"✅ Workload trace '{self.trace_file}' loaded (Min Users: {self.shift}, Max Users: {self.amplitude + self.shift}).")
            if self.fit_trace:
                print("   -> 'Fit to Duration' mode ACTIVE.")
        except Exception as e:
            print(f"❌ ERROR: Could not read or process workload file '{self.trace_file}': {e}")
            sys.exit(1)

    def tick(self):
        run_time = self.get_run_time()
        if run_time > self.time_limit:
            return None

        if self.fit_trace:
            scaling_ratio = self.max_index / self.time_limit
            trace_index = int(run_time * scaling_ratio)
            trace_index = min(trace_index, self.max_index - 1)
        else:
            trace_index = int(run_time) % self.max_index

        user_count = self.scaled_trace[trace_index]
        return (user_count, 100) # Using aggressive spawn rate

# --- User Behavior ---
class MicroserviceUser(HttpUser):
    think_time = get_env_var("LOCUST_THINK_TIME", is_numeric=True)
    wait_time = constant(think_time)
    
    entrypoint_path = get_env_var("LOCUST_ENTRYPOINT_PATH", default="/")
    print(f"✅ User think time set to {think_time}s. Entrypoint path set to {entrypoint_path}.")

    @task
    def access_entrypoint(self):
        self.client.get(self.entrypoint_path)

