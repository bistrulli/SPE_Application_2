# SPE Application 2 - Performance Testing & Validation Framework

Automated framework for performance testing of microservice architectures on Kubernetes with validation through analytical models (JMT/JMVA).

## 🎯 Objectives

This framework enables:

1. **Performance testing** of Kubernetes deployments with controlled workloads
2. **Metrics collection** from Istio service mesh, Prometheus, and Locust
3. **Consolidated statistics** generation (mean values, standard deviation, min/max)
4. **Results validation** by comparing experimental measurements with analytical model predictions (JMVA - Mean Value Analysis)
5. **Visualization** of temporal trends and mean values through plots

## 🛠️ Tools Used

- **Kubernetes**: Container orchestration (tested with minikube)
- **Istio**: Service mesh for telemetry and network metrics
- **Prometheus**: Time-series metrics collection and storage
- **Locust**: HTTP workload generation
- **JMT (Java Modelling Tools)**: Analytical queueing model solver (MVA)
- **Python 3.8+**: Scripting and automation

## 📋 Prerequisites

### Required Software

```bash
# Kubernetes cluster (minikube recommended)
minikube start --memory=4096 --cpus=4

# Istio service mesh
istioctl install --set profile=default -y
kubectl label namespace default istio-injection=enabled

# Prometheus (if not already included with Istio)
kubectl apply -f https://raw.githubusercontent.com/istio/istio/release-1.20/samples/addons/prometheus.yaml

# Enable Istio metrics
kubectl apply -f QNonK8s/istio/telemetry.yaml
```

### Python Dependencies

```bash
pip install -r requirements.txt
```

**File `requirements.txt`:**
```
requests>=2.31.0
pandas>=2.0.0
numpy>=1.24.0
matplotlib>=3.7.0
kubernetes>=28.0.0
prometheus-client>=0.19.0
pyyaml>=6.0
locust>=2.18.0
```

### JMT (optional, for validation only)

Download JMT from: http://jmt.sourceforge.net/

Place the JAR in a known directory (e.g., `/Users/emilio-imt/JMT-singlejar-1.2.2.jar`)

## 🚀 Quick Start

### Basic Example

```bash
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode constant \
  --users 10 \
  --think-time 1.0 \
  --duration 120
```

### Example with JMT Validation

```bash
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode constant \
  --users 1 \
  --think-time 1.0 \
  --duration 300 \
  --validate-jmt experiment_mapping.yaml
```

## 📊 Test Modes

### 1. Constant Load (`--mode constant`)

Generates a fixed number of concurrent users for the entire test duration.

```bash
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode constant \
  --users 50 \
  --think-time 0.5 \
  --duration 300
```

**Main parameters:**
- `--users N`: Number of concurrent users
- `--think-time T`: Wait time between requests (seconds)
- `--duration D`: Test duration (seconds)

### 2. Trace-Based Load (`--mode trace`)

Uses real load traces for more realistic patterns.

```bash
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode trace \
  --trace-file QNonK8s/locust/workloads/sin800.csv \
  --amplitude 50 \
  --shift 10 \
  --duration 600 \
  --fit-trace
```

**Parameters:**
- `--trace-file PATH`: CSV file with load trace
- `--amplitude A`: Maximum users above baseline
- `--shift S`: Minimum users (baseline)
- `--fit-trace`: Fit trace to specified duration

**Available traces:**
- `QNonK8s/locust/workloads/sin800.csv` - Sinusoidal pattern
- `QNonK8s/locust/workloads/twitter.csv` - Real Twitter trace

## 📈 Generated Output

Each experiment creates a `results/experiment_YYYYMMDD_HHMMSS/` directory with:

### 1. Summary Statistics (Main File)

**`experiment_YYYYMMDD_HHMMSS_summary.csv`**

Contains **only mean values** of all metrics, directly comparable with JMT:

| Metric | Mean | Std Dev | Min | Max | Unit |
|--------|------|---------|-----|-----|------|
| Users | 1.0000 | 0.0000 | 1.0 | 1.0 | users |
| Client RPS | 0.8357 | 0.0234 | 0.81 | 0.86 | req/s |
| Client Response Time | 100.12 | 5.23 | 95.5 | 110.0 | ms |
| System Throughput | 0.8357 | 0.0234 | 0.81 | 0.86 | req/s |
| Task1 Throughput | 0.8357 | 0.0234 | 0.81 | 0.86 | req/s |
| Task1 Response Time | 99.44 | 2.15 | 96.85 | 102.04 | ms |
| Task1 CPU Utilization | 0.0828 | 0.0012 | 0.0807 | 0.0849 | cores |
| Task2 Throughput | 0.8341 | 0.0245 | 0.81 | 0.8597 | req/s |
| Task2 Response Time | 101.33 | 1.89 | 98.99 | 103.68 | ms |
| Task2 CPU Utilization | 0.0841 | 0.0015 | 0.0819 | 0.0863 | cores |

### 2. Detailed Time Series

**`experiment_YYYYMMDD_HHMMSS_timeseries.csv`**

Complete time series with all metrics collected every N seconds (default: 5s).

**Columns:**
- `timestamp`, `elapsed` - Time
- `locust_users`, `locust_requests_per_second`, `locust_response_time_avg`, `locust_failure_rate` - Locust client metrics
- `throughput`, `response_time_avg` - Aggregated system metrics
- `throughput_taskN`, `response_time_taskN`, `cpu_usage_taskN` - Per-task metrics
- `taskN_replicas` - Number of replicas per deployment

### 3. Plots

**`plots_YYYYMMDD_HHMMSS.png`**

Four subplots with dashed lines showing mean values:
1. **Throughput over time** - Server vs Client
2. **Response Time over time** - Server vs Client
3. **CPU Utilization over time** - Task1 and Task2
4. **Active Users over time** - Load profile

### 4. JMT Validation Report (optional)

**`jmt_validation_YYYYMMDD_HHMMSS.txt`**

Per-station comparison between experimental measurements and MVA predictions:

```
============================================================
JMT Model Validation Report
============================================================

## Per-Station Comparison
------------------------------------------------------------

### task1 <-> Tier1

  Metric          | Measured      | Theoretical   | Error %
  --------------------------------------------------------
  Throughput      |         0.84  |         0.83  |   1.20%
  Response Time   |       0.0994s |       0.1000s |   0.60%
  CPU Utilization |       0.0828  |       0.0830  |   0.24%

### task2 <-> Tier2
...

## Summary
------------------------------------------------------------
  *** OVERALL ASSESSMENT: EXCELLENT ***
```

## 🔬 JMT/JMVA Validation

### JMVA Template Configuration

The `experiment_mapping.yaml` file defines the mapping between Kubernetes deployments and JMT model stations:

```yaml
# experiment_mapping.yaml
experiment:
  name: "3tier_closed_validation"
  description: "Validate 3-tier microservice architecture"

jmt:
  template: "QNonK8s/JMT_mva_validation/Closed_3tier.jmva"
  jar_path: "/Users/emilio-imt/JMT-singlejar-1.2.2.jar"
  solver_type: "mva"  # Analytical MVA solver
  network_type: "closed"

  closed_network:
    customers: 1  # Automatically updated from --users
    reference_station: "Delay 1"

kubernetes:
  manifest: "QNonK8s/applications/3tier.yaml"
  namespace: "default"

mapping:
  delay:
    jmt_station: "Delay 1"
    station_type: "Delay"
    update_params:
      - think_time_lambda

  task1-deployment:
    jmt_station: "Tier1"
    station_type: "Server"
    service_time_env_var: "SERVICE_TIME_SECONDS"
    update_params:
      - service_time_lambda
      - num_servers

  task2-deployment:
    jmt_station: "Tier2"
    station_type: "Server"
    service_time_env_var: "SERVICE_TIME_SECONDS"
    update_params:
      - service_time_lambda
      - num_servers

workload:
  think_time: 1.0  # Updated from --think-time

validation:
  acceptable_error_threshold: 15.0
  metrics:
    - throughput
    - response_time
    - utilization
```

### Creating JMVA Templates

1. **Open JMT GUI** and create a model with JMVA (analytical solver)
2. **Define stations**:
   - Delay station for think time
   - Load-Independent stations (listation) for tasks
3. **Set initial parameters**:
   - Population (customers) = 1
   - Service times = 0.1s (will be overwritten)
   - Think time = 1.0s (will be overwritten)
4. **Save as `.jmva`** in `QNonK8s/JMT_mva_validation/`

### Execution with Validation

```bash
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode constant \
  --users 1 \
  --think-time 1.0 \
  --duration 300 \
  --validate-jmt experiment_mapping.yaml
```

### Internal Workflow

1. **K8s Deploy** of specified manifest
2. **Start Locust** with configured parameters
3. **Metrics collection** from Prometheus and Locust (every 5s)
4. **Statistics computation** (mean, std dev, min, max)
5. **Parameter extraction** from K8s manifest (service time, replicas)
6. **JMVA template modification** with real parameters:
   - `population` = `--users`
   - `think_time` = `--think-time`
   - `service_time` from environment variable `SERVICE_TIME_SECONDS`
   - `servers` = number of replicas
7. **MVA solver execution**:
   ```bash
   java -cp JMT.JAR jmt.commandline.Jmt mva Closed_3tier.jmva
   ```
8. **Results parsing** from file `Closed_3tier-result.jmva`
9. **Comparison** theory vs measurements per station
10. **Validation report generation**

## 🎓 Example Scenarios

### Scenario 1: M/M/c Model Validation

Verify model correctness with light load:

```bash
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode constant \
  --users 1 \
  --think-time 1.0 \
  --duration 300 \
  --validate-jmt experiment_mapping.yaml
```

**Expected:**
- Error < 5% on throughput and response time
- Assessment: EXCELLENT

### Scenario 2: Scalability Study

Test with different numbers of replicas:

```bash
# 1 replica
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier_1replica.yaml \
  --mode constant --users 10 --think-time 0.5 --duration 180 \
  --output-dir results/scalability/1replica

# 2 replicas
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier_2replicas.yaml \
  --mode constant --users 10 --think-time 0.5 --duration 180 \
  --output-dir results/scalability/2replicas
```

### Scenario 3: Variable Load

Simulate realistic pattern with Twitter trace:

```bash
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode trace \
  --trace-file QNonK8s/locust/workloads/twitter.csv \
  --amplitude 100 \
  --shift 20 \
  --duration 600 \
  --fit-trace
```

### Scenario 4: CPU Utilization Study

Test how CPU utilization varies with different loads:

```bash
for users in 10 20 50 100; do
  python experiment_runner.py \
    --manifest QNonK8s/applications/3tier.yaml \
    --mode constant \
    --users $users \
    --think-time 0.5 \
    --duration 180 \
    --output-dir results/cpu_study/${users}users
done
```

## 🔧 Advanced Parameters

### All Available Parameters

```bash
python experiment_runner.py \
  --manifest PATH                    # Kubernetes manifest (required)
  --mode {constant,trace}            # Test mode (required)

  # Constant mode
  --users N                          # Number of users
  --think-time T                     # Think time (seconds)
  --duration D                       # Test duration (seconds)

  # Trace mode
  --trace-file PATH                  # Trace CSV file
  --amplitude A                      # Max users above baseline
  --shift S                          # Min users (baseline)
  --fit-trace                        # Fit trace to duration
  --trace-think-time T               # Think time for trace mode

  # Configuration
  --namespace NS                     # K8s namespace (default: default)
  --prometheus-url URL               # Prometheus URL (auto-detect)
  --locust-host URL                  # Locust target host (auto-detect)
  --metrics-interval N               # Collection interval (seconds, default: 5)
  --output-dir DIR                   # Output directory (auto-generate)

  # Options
  --no-cleanup                       # Don't delete deployment
  --validate-jmt CONFIG              # Validate with JMT using config file
```

### Prometheus and Ingress Gateway

The script automatically detects:
- **Prometheus**: Automatic port-forward to `localhost:9090`
- **Istio Ingress Gateway**: Minikube IP + NodePort

Override manually if needed:

```bash
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode constant --users 10 --think-time 1.0 --duration 120 \
  --prometheus-url http://localhost:9090 \
  --locust-host http://192.168.49.2:31851
```

### No Cleanup

Keep deployment active after test (useful for debugging):

```bash
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode constant --users 10 --think-time 1.0 --duration 60 \
  --no-cleanup

# Manual cleanup
kubectl delete -f QNonK8s/applications/3tier.yaml
```

## 📊 Data Analysis

### Import Summary Statistics

```python
import pandas as pd

# Load mean statistics
summary = pd.read_csv('results/experiment_20241116_143022/experiment_20241116_143022_summary.csv')

print(summary)

# Extract specific metrics
throughput = summary[summary['Metric'] == 'System Throughput']['Mean'].values[0]
response_time = summary[summary['Metric'] == 'Client Response Time']['Mean'].values[0]

print(f"Mean throughput: {throughput:.2f} req/s")
print(f"Mean response time: {response_time:.2f} ms")
```

### Time Series Analysis

```python
import pandas as pd
import matplotlib.pyplot as plt

# Load time-series
ts = pd.read_csv('results/experiment_20241116_143022/experiment_20241116_143022_timeseries.csv')

# Plot throughput
plt.figure(figsize=(12, 6))
plt.plot(ts['elapsed'], ts['throughput'], label='Throughput', alpha=0.6)
plt.axhline(y=ts['throughput'].mean(), color='r', linestyle='--', label='Mean')
plt.xlabel('Time (s)')
plt.ylabel('Throughput (req/s)')
plt.title('Throughput Over Time')
plt.legend()
plt.grid(True)
plt.show()
```

### Compare Experiments

```python
import pandas as pd

# Load summary from two experiments
exp1 = pd.read_csv('results/exp1/experiment_X_summary.csv')
exp2 = pd.read_csv('results/exp2/experiment_Y_summary.csv')

# Compare mean throughput
tp1 = exp1[exp1['Metric'] == 'System Throughput']['Mean'].values[0]
tp2 = exp2[exp2['Metric'] == 'System Throughput']['Mean'].values[0]

print(f"Exp1 Throughput: {tp1:.2f} req/s")
print(f"Exp2 Throughput: {tp2:.2f} req/s")
print(f"Improvement: {((tp2/tp1 - 1) * 100):.1f}%")
```

## 🐛 Troubleshooting

### Prometheus Unreachable

```bash
# Verify Prometheus pod
kubectl get pods -n istio-system | grep prometheus

# Manual port-forward (script does this automatically)
kubectl port-forward -n istio-system svc/prometheus 9090:9090

# Test connection
curl http://localhost:9090/api/v1/query?query=up
```

### Missing Istio Metrics

```bash
# Verify Telemetry CRD
kubectl get telemetry -n istio-system

# Apply if missing
kubectl apply -f QNonK8s/istio/telemetry.yaml

# Verify sidecar injection
kubectl get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[*].name}{"\n"}'
```

You should see `istio-proxy` among the containers of each pod.

### Locust Cannot Reach Target

```bash
# Verify ingress gateway
kubectl get svc -n istio-system istio-ingressgateway

# For minikube, get IP + NodePort
minikube ip
kubectl get svc -n istio-system istio-ingressgateway -o jsonpath='{.spec.ports[?(@.name=="http2")].nodePort}'

# Manual test
curl http://$(minikube ip):31851
```

### JMT Validation Fails

```bash
# Verify JMT JAR path
ls -lh /Users/emilio-imt/JMT-singlejar-1.2.2.jar

# Test manual JMT execution
java -cp /Users/emilio-imt/JMT-singlejar-1.2.2.jar jmt.commandline.Jmt mva QNonK8s/JMT_mva_validation/Closed_3tier.jmva

# Verify results
cat QNonK8s/JMT_mva_validation/Closed_3tier-result.jmva
```

## 📁 Repository Structure

```
SPE_Application_2/
├── README.md                          # This guide
├── requirements.txt                   # Python dependencies
├── experiment_runner.py               # Main script
├── jmt_validator.py                   # JMT/JMVA validation
├── metrics_collector.py               # Prometheus metrics collection
├── experiment_mapping.yaml            # JMT validation configuration
│
├── QNonK8s/
│   ├── applications/                  # Kubernetes manifests
│   │   ├── 3tier.yaml                 # Standard 3-tier deployment
│   │   └── ...
│   │
│   ├── locust/                        # Locust load testing
│   │   ├── locustfile.py              # Constant load scenario
│   │   ├── locustfile_trace.py        # Trace-based scenario
│   │   ├── locust_prometheus_exporter.py  # Metrics exporter
│   │   └── workloads/                 # Load traces
│   │       ├── sin800.csv
│   │       └── twitter.csv
│   │
│   ├── istio/                         # Istio configuration
│   │   └── telemetry.yaml             # Enable Istio metrics
│   │
│   └── JMT_mva_validation/            # JMT templates
│       └── Closed_3tier.jmva          # 3-tier MVA model
│
└── results/                           # Experiment outputs
    └── experiment_YYYYMMDD_HHMMSS/
        ├── experiment_*_summary.csv      # Mean statistics
        ├── experiment_*_timeseries.csv   # Time series
        ├── plots_*.png                   # Plots
        └── jmt_validation_*.txt          # Validation report
```

## 📚 References

- **JMT (Java Modelling Tools)**: http://jmt.sourceforge.net/
- **JMT User Manual**: https://jmt.sourceforge.net/Papers/JMT_users_Manual.pdf
- **Locust Documentation**: https://docs.locust.io/
- **Istio Metrics**: https://istio.io/latest/docs/reference/config/metrics/
- **Prometheus Query Language**: https://prometheus.io/docs/prometheus/latest/querying/basics/
- **Kubernetes Python Client**: https://github.com/kubernetes-client/python

## 💡 Best Practices

1. **Incremental testing**: Start with `--duration 60` for quick tests
2. **Warm-up**: First samples might be unstable
3. **Steady-state**: Ensure system reaches steady-state
4. **Validation**: Use `--validate-jmt` only for light loads compatible with MVA
5. **Documentation**: Version control manifests and document parameters/results

## 🎓 Student Exercises

1. **Verify Little's Law**: Calculate N = X × R from data and compare with `locust_users`
2. **Saturation**: Find the load that saturates CPU at 100%
3. **Linear Scalability**: Verify if throughput doubles with 2× replicas
4. **MVA vs Measurements**: Compare MVA predictions with real experiments
5. **Response Time Percentiles**: Analyze distribution from time series

## ✨ Authors

Developed for the Software Performance Engineering course.

## 📄 License

This project is released for educational purposes.
