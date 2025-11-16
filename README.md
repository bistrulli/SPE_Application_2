# Kubernetes Microservices Performance Validation Framework

A comprehensive framework for validating Kubernetes microservice architectures against queueing theory models using JMT (Java Modelling Tools). This tool enables performance testing, metrics collection, and theoretical model validation.

## Overview

This framework provides an end-to-end solution for:
- **Deploying** microservice architectures on Kubernetes
- **Generating** load using Locust
- **Collecting** metrics from Prometheus (Istio service mesh)
- **Validating** against JMT analytical queueing models
- **Analyzing** performance with what-if scenarios

The goal is to bridge the gap between theoretical queueing network models and real-world Kubernetes deployments, enabling students and practitioners to validate their performance models empirically.

## Core Concepts

### Queueing Theory and JMT

**Queueing Network Models** represent systems where requests (customers) flow through service stations (servers). Key metrics include:
- **Throughput**: Requests processed per unit time (req/s)
- **Response Time**: Time from request arrival to completion (s)
- **Utilization**: Fraction of time a server is busy (0-1)

**Closed Networks**: Fixed number of customers circulating in the system. After completing a request, a customer "thinks" for some time before submitting the next request.

**JMT (Java Modelling Tools)** is an open-source suite for performance evaluation:
- **JMVA**: Analytical Mean Value Analysis solver (fast, exact for simple models)
- **JSIMgraph**: Discrete-event simulation (handles complex scenarios)

### Kubernetes and Istio

**Kubernetes** orchestrates containerized microservices with:
- **Deployments**: Define desired state (replicas, resource limits)
- **Services**: Network abstraction for accessing pods
- **Horizontal scaling**: Adjust replica count based on load

**Istio Service Mesh** provides:
- Traffic management
- **Observability**: Automatic metrics collection
- Request-level telemetry (throughput, latency, errors)

### Prometheus and Locust

**Prometheus** is a time-series database for metrics:
- Scrapes metrics from Istio sidecars
- PromQL query language for aggregations
- Key metrics: `istio_requests_total`, `istio_request_duration_milliseconds`

**Locust** is a Python-based load testing tool:
- Simulates user behavior
- Configurable think time and user count
- Exports metrics via Prometheus endpoint

## Architecture

```
┌─────────────────────┐
│   Locust Client     │
│ (Load Generation)   │
└─────────┬───────────┘
          │ HTTP Requests
          ▼
┌─────────────────────┐
│  Istio Ingress      │
│    Gateway          │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐    ┌─────────────────────┐
│   Task1 Service     │───▶│   Task2 Service     │
│   (Tier1 in JMT)    │    │   (Tier2 in JMT)    │
└─────────────────────┘    └─────────────────────┘
          │                           │
          └───────────┬───────────────┘
                      │ Metrics
                      ▼
┌─────────────────────┐
│     Prometheus      │
│  (Metrics Storage)  │
└─────────────────────┘
          │
          ▼
┌─────────────────────┐
│  Metrics Collector  │
│    (Python)         │
└─────────────────────┘
          │
          ▼
┌─────────────────────┐
│    JMT Validator    │
│  (Compare Results)  │
└─────────────────────┘
```

## File Structure

```
SPE_Application_2/
├── experiment_runner.py      # Main experiment orchestrator
├── metrics_collector.py      # Prometheus metrics collection
├── jmt_validator.py          # JMT model validation
├── whatif_analysis.py        # Multi-experiment analysis
├── experiment_mapping.yaml   # Configuration mapping
├── QNonK8s/
│   ├── applications/
│   │   └── 3tier.yaml        # K8s deployment manifest
│   ├── locust/
│   │   ├── locustfile.py     # Constant load generator
│   │   └── locustfile_trace.py  # Trace-based load
│   └── JMT_mva_validation/
│       └── Closed_3tier.jmva # JMT model template
└── results/                  # Experiment outputs
    └── YYYYMMDD_HHMMSS/      # Timestamped results
```

## Configuration

### experiment_mapping.yaml

This file defines the mapping between Kubernetes deployments and JMT queueing stations:

```yaml
# JMT Configuration
jmt:
  template: "QNonK8s/JMT_mva_validation/Closed_3tier.jmva"
  jar_path: "/path/to/JMT.jar"
  network_type: "closed"  # or "open"

  closed_network:
    customers: 1  # Will be overwritten by --users
    reference_station: "Delay"

# Mapping K8s deployments to JMT stations
mapping:
  delay:
    jmt_station: "Delay"
    station_type: "Delay"

  task1-deployment:
    jmt_station: "Tier1"
    station_type: "Server"
    service_time_env_var: "SERVICE_TIME_SECONDS"

  task2-deployment:
    jmt_station: "Tier2"
    station_type: "Server"
    service_time_env_var: "SERVICE_TIME_SECONDS"

# Workload parameters
workload:
  think_time: 1.0  # Will be overwritten by --think-time
```

### Kubernetes Manifest

The deployment manifest (e.g., `3tier.yaml`) defines the microservice architecture:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: task1-deployment
spec:
  replicas: 1
  template:
    spec:
      containers:
      - name: task1
        env:
        - name: SERVICE_TIME_SECONDS
          value: "0.1"  # Mean service time
```

## Running Experiments

### Prerequisites

1. **Kubernetes cluster** with Istio installed
2. **Prometheus** configured to scrape Istio metrics
3. **JMT** JAR file downloaded
4. **Python dependencies**: `pip install pandas matplotlib requests pyyaml locust`

### Single Experiment

```bash
python3 experiment_runner.py \
    --manifest QNonK8s/applications/3tier.yaml \
    --config experiment_mapping.yaml \
    --mode constant \
    --users 10 \
    --think-time 1.0 \
    --duration 300
```

**Parameters:**
- `--manifest`: Kubernetes deployment YAML
- `--config`: Experiment mapping configuration (required)
- `--mode`: Load type (`constant` or `trace`)
- `--users`: Number of concurrent users (closed network population)
- `--think-time`: Mean time between requests (seconds)
- `--duration`: Experiment duration (seconds)
- `--skip-jmt-validation`: Skip JMT validation (runs by default)

**Output:**
```
results/20251116_174426/
├── metrics_timeseries.csv       # Time-series data
├── summary_statistics.csv       # Aggregated metrics
├── metrics_plots.png            # Visualization
├── jmt_validation_report.txt    # Validation results
├── jmt_validation_results.csv   # Structured comparison
└── locust_*.csv                 # Locust statistics
```

### What-If Analysis

Run multiple experiments with varying user loads to analyze system behavior:

```bash
python3 whatif_analysis.py \
    --users-range 1,5,10,20,50 \
    --manifest QNonK8s/applications/3tier.yaml \
    --config experiment_mapping.yaml \
    --think-time 1.0 \
    --duration 120 \
    --cooldown 30
```

**Parameters:**
- `--users-range`: Comma-separated list of user counts to test
- `--cooldown`: Seconds to wait between experiments (default: 10)

**Output:**
```
results/whatif_20251116_180000/
├── aggregated_results.csv       # All experiments data
├── task1_throughput.png         # Measured vs JMT per task
├── task1_response_time.png
├── task1_utilization.png
├── task2_throughput.png
├── task2_response_time.png
├── task2_utilization.png
└── error_distribution.png       # Error boxplot by metric type
```

## Understanding Results

### Validation Report

The `jmt_validation_report.txt` compares measured vs theoretical values:

```
### task1 <-> Tier1
  Metric          | Measured      | Theoretical   | Error %
  --------------------------------------------------------
  Throughput      |         0.73  |         0.83  |  12.20%
  Response Time   |       0.1130s |       0.1000s |  13.00%
  CPU Utilization |       0.0815  |       0.0833  |   2.16%
```

### Assessment Criteria

- **EXCELLENT**: < 5% average error
- **GOOD**: 5-10% average error
- **ACCEPTABLE**: 10-15% average error
- **NEEDS REVIEW**: > 15% average error

### Error Sources

1. **Prometheus warm-up**: Initial metrics may be delayed (20-45s)
2. **Distribution mismatch**: Actual service times vs exponential assumption
3. **Network overhead**: Istio proxy latency not in model
4. **Measurement granularity**: 1-minute rate windows

## Extending the Framework

### Adding New Services

1. Update Kubernetes manifest with new deployment
2. Add mapping in `experiment_mapping.yaml`:
   ```yaml
   task3-deployment:
     jmt_station: "Tier3"
     station_type: "Server"
     service_time_env_var: "SERVICE_TIME_SECONDS"
   ```
3. Update JMT template to include the new station
4. Run experiments and validate

### Custom Metrics

Add new Prometheus queries in `metrics_collector.py`:

```python
QUERY_TEMPLATES = {
    'throughput': 'sum(rate(istio_requests_total{...}[1m]))',
    'response_time': 'sum(rate(...))/sum(rate(...))',
    'cpu_usage': 'sum(rate(container_cpu_usage_seconds_total{...}[1m]))',
    # Add your custom metric here
    'memory_usage': 'sum(container_memory_usage_bytes{...})',
}
```

### Different Load Patterns

Use trace-based load for realistic workloads:

```bash
python3 experiment_runner.py \
    --manifest QNonK8s/applications/3tier.yaml \
    --config experiment_mapping.yaml \
    --mode trace \
    --trace-file QNonK8s/locust/workloads/sin800.csv \
    --amplitude 50 \
    --shift 10 \
    --duration 300
```

## Key Python Modules

### experiment_runner.py

Main orchestrator that:
- Deploys Kubernetes manifests
- Starts Locust load generation
- Collects Prometheus metrics using parametric queries
- Runs JMT validation automatically
- Generates reports and plots
- Handles cleanup on interruption (Ctrl+C)

### metrics_collector.py

Parametric Prometheus query system:
- Template-based queries with placeholder substitution (`{deployment}`, `{service}`, `{task}`)
- Supports multiple deployments dynamically
- Filters None/NaN values during Prometheus warm-up period

### jmt_validator.py

JMT integration:
- Extracts service times from K8s manifest environment variables
- Modifies JMT templates programmatically (XML manipulation)
- Runs JMVA solver via command line
- Parses results and compares theoretical vs measured metrics

### whatif_analysis.py

Multi-experiment wrapper:
- Runs experiments with varying user counts
- Real-time output from each experiment
- Graceful cleanup on Ctrl+C interruption
- Aggregates results across experiments
- Generates comparison plots (measured vs JMT predicted)
- Creates error distribution box plot

## Theoretical Background

### Little's Law

```
L = λ * W
```
- L: Average number in system
- λ: Arrival rate
- W: Average time in system

### Closed Network Model

For a closed network with N customers:
```
X = N / (Z + R)
```
- X: System throughput
- N: Number of customers
- Z: Think time
- R: Total response time

### MVA Algorithm

Mean Value Analysis computes steady-state metrics iteratively:
1. Start with empty system (N=0)
2. For each customer added, compute queue lengths, response times, throughput
3. Uses arrival theorem: arriving customer sees time-average queue length

Key advantage: No need to solve balance equations, direct computation.

## Best Practices

1. **Warm-up period**: Allow 30-60s for Prometheus metrics to stabilize
2. **Experiment duration**: Minimum 120s for statistical significance
3. **Cooldown between experiments**: Wait for system to reset state
4. **Multiple runs**: Repeat experiments to account for variability
5. **Resource limits**: Set CPU/memory limits in K8s to match model assumptions
6. **Service time calibration**: Measure actual service times, don't assume exponential
7. **Incremental validation**: Start with simple models (1 user) before complex scenarios

## References

- [JMT - Java Modelling Tools](http://jmt.sourceforge.net/)
- [JMT User Manual](https://jmt.sourceforge.net/Papers/JMT_users_Manual.pdf)
- [Istio Service Mesh](https://istio.io/latest/docs/)
- [Prometheus Documentation](https://prometheus.io/docs/)
- [Locust Load Testing](https://locust.io/)
- [Kubernetes Documentation](https://kubernetes.io/docs/)
- Performance Modeling and Design of Computer Systems (Harchol-Balter)

## License

This framework is provided for educational purposes. See individual components for their respective licenses.
