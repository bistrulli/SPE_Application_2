"""
Metrics collector module for gathering data from Prometheus.

This module provides utilities to collect key performance metrics from the
M/M/1 server monitoring stack, including throughput, response times, and
resource utilization.
"""

import time
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from typing import Dict, List, Optional, Tuple, Union
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class MetricPoint:
    """A single metric data point."""
    timestamp: float
    value: float


@dataclass
class MetricSeries:
    """A time series of metric data."""
    metric_name: str
    labels: Dict[str, str]
    points: List[MetricPoint]

    @property
    def timestamps(self) -> List[float]:
        return [p.timestamp for p in self.points]

    @property
    def values(self) -> List[float]:
        return [p.value for p in self.points]

    def to_dataframe(self) -> pd.DataFrame:
        """Convert to pandas DataFrame."""
        return pd.DataFrame({
            'timestamp': self.timestamps,
            'value': self.values
        })


class PrometheusCollector:
    """
    Prometheus metrics collector for M/M/1 server monitoring.

    Provides convenient methods to collect key performance metrics during
    workload generation experiments.
    """

    # Queries for Kubernetes/Istio microservices environment
    QUERIES = {
        # Throughput: successful requests per second at gateway level
        'throughput': 'sum(rate(istio_requests_total{destination_service_name="task1-svc", response_code="200"}[1m]))',

        # Response time: average response time from Istio
        'response_time_avg': 'sum(rate(istio_request_duration_milliseconds_sum{destination_service_name="task1-svc"}[1m])) / sum(rate(istio_request_duration_milliseconds_count{destination_service_name="task1-svc"}[1m]))',

        # CPU usage for task1 pods
        'cpu_usage_task1': 'sum(rate(container_cpu_usage_seconds_total{namespace="default", pod=~"task1-.*", container!="POD", container!="istio-proxy"}[1m]))',

        # CPU usage for task2 pods
        'cpu_usage_task2': 'sum(rate(container_cpu_usage_seconds_total{namespace="default", pod=~"task2-.*", container!="POD", container!="istio-proxy"}[1m]))',

        # Task1 specific throughput
        'throughput_task1': 'sum(rate(istio_requests_total{destination_service_name="task1-svc"}[1m]))',

        # Task2 specific throughput
        'throughput_task2': 'sum(rate(istio_requests_total{destination_service_name="task2-svc"}[1m]))',

        # Task1 response time
        'response_time_task1': 'sum(rate(istio_request_duration_milliseconds_sum{destination_service_name="task1-svc"}[1m])) / sum(rate(istio_request_duration_milliseconds_count{destination_service_name="task1-svc"}[1m]))',

        # Task2 response time
        'response_time_task2': 'sum(rate(istio_request_duration_milliseconds_sum{destination_service_name="task2-svc"}[1m])) / sum(rate(istio_request_duration_milliseconds_count{destination_service_name="task2-svc"}[1m]))',

        # Locust metrics (from Prometheus exporter on port 9646)
        'locust_users': 'locust_users',
        'locust_requests_per_second': 'locust_requests_per_second',
        'locust_response_time_avg': 'locust_response_time_avg',
        'locust_failure_rate': 'locust_failure_rate',
    }

    def __init__(self, prometheus_url: str = "http://localhost:9090", custom_queries: Optional[Dict[str, str]] = None, locust_exporter_url: str = "http://localhost:9646"):
        """
        Initialize Prometheus collector.

        Args:
            prometheus_url: Base URL of Prometheus server
            custom_queries: Optional custom queries to override defaults (for K8s/Istio)
            locust_exporter_url: URL of Locust Prometheus exporter (for direct reading)
        """
        self.base_url = prometheus_url.rstrip('/')
        self.locust_exporter_url = locust_exporter_url.rstrip('/')
        self.session = requests.Session()
        self._mm1_container_id = None

        # Use custom queries if provided, otherwise use defaults
        if custom_queries:
            self.QUERIES = {**self.QUERIES, **custom_queries}
            print(f"PrometheusCollector: Using custom queries for {list(custom_queries.keys())}")

    def query(self, query: str, time_point: Optional[float] = None) -> Dict:
        """
        Execute a PromQL query.

        Args:
            query: PromQL query string
            time_point: Unix timestamp for point-in-time query (optional)

        Returns:
            Raw Prometheus API response
        """
        url = f"{self.base_url}/api/v1/query"
        params = {'query': query}
        if time_point:
            params['time'] = time_point

        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise ConnectionError(f"Failed to query Prometheus: {e}")

    def query_range(
        self,
        query: str,
        start_time: float,
        end_time: float,
        step: str = "5s"
    ) -> Dict:
        """
        Execute a PromQL range query.

        Args:
            query: PromQL query string
            start_time: Start timestamp (Unix time)
            end_time: End timestamp (Unix time)
            step: Query resolution step (e.g., "5s", "1m")

        Returns:
            Raw Prometheus API response
        """
        url = f"{self.base_url}/api/v1/query_range"
        params = {
            'query': query,
            'start': start_time,
            'end': end_time,
            'step': step
        }

        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise ConnectionError(f"Failed to query Prometheus range: {e}")

    def get_current_metrics(self, metrics: List[str] = None) -> Dict[str, float]:
        """
        Get current values for standard M/M/1 metrics.

        Args:
            metrics: List of metric names to collect (default: all standard metrics)

        Returns:
            Dictionary mapping metric names to current values
        """
        if metrics is None:
            metrics = list(self.QUERIES.keys())

        results = {}
        for metric in metrics:
            if metric not in self.QUERIES:
                raise ValueError(f"Unknown metric: {metric}")

            try:
                # Handle CPU usage specially to find container dynamically
                if metric == 'cpu_usage':
                    container_id = self._find_mm1_container_id()
                    if container_id:
                        query = f'rate(container_cpu_usage_seconds_total{{id="{container_id}"}}[1m])'
                    else:
                        query = self.QUERIES[metric]  # Fallback to default
                else:
                    query = self.QUERIES[metric]

                response = self.query(query)

                # If CPU query fails and we had a cached container ID, invalidate cache and retry
                if metric == 'cpu_usage' and (response['status'] != 'success' or not response['data']['result']):
                    if self._mm1_container_id:
                        print(f"CPU query failed with cached container ID, invalidating cache and retrying...")
                        self._mm1_container_id = None  # Invalidate cache
                        container_id = self._find_mm1_container_id()  # Re-discover
                        if container_id:
                            query = f'rate(container_cpu_usage_seconds_total{{id="{container_id}"}}[1m])'
                            response = self.query(query)  # Retry query

                if response['status'] == 'success' and response['data']['result']:
                    # Take the first result if multiple series returned
                    value = float(response['data']['result'][0]['value'][1])

                    # Convert response times from milliseconds to seconds
                    if metric == 'response_time_avg':
                        value = value / 1000.0  # Convert ms to seconds

                    results[metric] = value
                else:
                    results[metric] = None
            except Exception as e:
                print(f"Warning: Failed to collect {metric}: {e}")
                results[metric] = None

        return results

    def collect_metrics_during_experiment(
        self,
        duration: float,
        metrics: List[str] = None,
        interval: float = 5.0
    ) -> Dict[str, List[Tuple[float, float]]]:
        """
        Collect metrics at regular intervals during an experiment.

        Args:
            duration: How long to collect metrics (seconds)
            metrics: Which metrics to collect (default: key performance metrics)
            interval: Collection interval in seconds

        Returns:
            Dictionary mapping metric names to lists of (timestamp, value) tuples
        """
        if metrics is None:
            metrics = ['throughput', 'response_time_avg', 'response_time_95p', 'cpu_usage']

        print(f"Collecting metrics for {duration}s (every {interval}s)...")

        results = {metric: [] for metric in metrics}
        start_time = time.time()
        next_collection = start_time

        while time.time() - start_time < duration:
            current_time = time.time()
            if current_time >= next_collection:
                timestamp = current_time
                current_values = self.get_current_metrics(metrics)

                for metric in metrics:
                    value = current_values.get(metric)
                    if value is not None:
                        results[metric].append((timestamp, value))

                next_collection += interval
                print(f"  Collected at t={current_time-start_time:.1f}s")

            time.sleep(0.5)  # Small sleep to avoid busy waiting

        return results

    def get_metrics_for_timerange(
        self,
        start_time: float,
        end_time: float,
        metrics: List[str] = None,
        step: str = "5s"
    ) -> Dict[str, MetricSeries]:
        """
        Retrieve metrics for a specific time range.

        Args:
            start_time: Start timestamp (Unix time)
            end_time: End timestamp (Unix time)
            metrics: Metrics to retrieve
            step: Query resolution

        Returns:
            Dictionary mapping metric names to MetricSeries objects
        """
        if metrics is None:
            metrics = ['throughput', 'response_time_avg', 'cpu_usage']

        results = {}
        for metric in metrics:
            if metric not in self.QUERIES:
                continue

            try:
                # Handle CPU usage specially to find container dynamically
                if metric == 'cpu_usage':
                    container_id = self._find_mm1_container_id()
                    if container_id:
                        query = f'rate(container_cpu_usage_seconds_total{{id="{container_id}"}}[1m])'
                    else:
                        query = self.QUERIES[metric]  # Fallback to default
                else:
                    query = self.QUERIES[metric]

                response = self.query_range(
                    query,
                    start_time,
                    end_time,
                    step
                )

                if response['status'] == 'success' and response['data']['result']:
                    # Take first result series
                    series_data = response['data']['result'][0]
                    labels = series_data.get('metric', {})
                    points = []

                    for timestamp, value in series_data['values']:
                        converted_value = float(value)

                        # Convert response times from milliseconds to seconds
                        if metric == 'response_time_avg':
                            converted_value = converted_value / 1000.0  # Convert ms to seconds

                        points.append(MetricPoint(
                            timestamp=float(timestamp),
                            value=converted_value
                        ))

                    results[metric] = MetricSeries(
                        metric_name=metric,
                        labels=labels,
                        points=points
                    )

            except Exception as e:
                print(f"Warning: Failed to retrieve {metric}: {e}")

        return results

    def _find_mm1_container_id(self) -> Optional[str]:
        """
        Get the container ID for mm1-server directly using Docker CLI.
        """
        if self._mm1_container_id:
            return self._mm1_container_id

        try:
            import subprocess
            # Get container ID directly by name - much simpler and reliable!
            container_id = subprocess.check_output(
                ['docker', 'inspect', 'mm1-server', '--format', '/docker/{{.Id}}'],
                text=True, timeout=5
            ).strip()

            self._mm1_container_id = container_id
            return self._mm1_container_id

        except subprocess.SubprocessError:
            # Container not found or docker command failed
            return None
        except Exception:
            return None

    def health_check(self) -> bool:
        """
        Check if Prometheus is accessible and responding.

        Returns:
            True if healthy, False otherwise
        """
        try:
            response = self.query('up')
            return response['status'] == 'success'
        except:
            return False

    def get_locust_metrics_direct(self) -> Dict[str, float]:
        """
        Read Locust metrics directly from the Prometheus exporter endpoint.
        This bypasses Prometheus and reads directly from localhost:9646/metrics.

        Returns:
            Dictionary with Locust metric values
        """
        try:
            response = self.session.get(f"{self.locust_exporter_url}/metrics", timeout=5)
            response.raise_for_status()

            metrics = {}
            for line in response.text.split('\n'):
                if line.startswith('#') or not line.strip():
                    continue

                # Parse Prometheus text format: metric_name value
                parts = line.split()
                if len(parts) >= 2:
                    metric_name = parts[0].split('{')[0]  # Remove labels if present
                    try:
                        value = float(parts[-1])
                        metrics[metric_name] = value
                    except ValueError:
                        continue

            # Map to standard names
            result = {
                'locust_users': metrics.get('locust_users', 0),
                'locust_requests_per_second': metrics.get('locust_requests_per_second', 0),
                'locust_response_time_avg': metrics.get('locust_response_time_avg', 0),
                'locust_response_time_p95': metrics.get('locust_response_time_p95', 0),
                'locust_response_time_p99': metrics.get('locust_response_time_p99', 0),
                'locust_failure_rate': metrics.get('locust_failure_rate', 0),
                'locust_total_requests': metrics.get('locust_total_requests', 0),
            }

            return result

        except Exception as e:
            # Return zeros if exporter not available
            return {
                'locust_users': 0,
                'locust_requests_per_second': 0,
                'locust_response_time_avg': 0,
                'locust_response_time_p95': 0,
                'locust_response_time_p99': 0,
                'locust_failure_rate': 0,
                'locust_total_requests': 0,
            }

    def close(self):
        """Close the HTTP session."""
        self.session.close()

    def get_deployment_replicas(self, namespace: str = "default") -> Dict[str, int]:
        """
        Get current replica counts for all deployments in a namespace.
        Uses kubectl to query deployment status.

        Args:
            namespace: Kubernetes namespace to query

        Returns:
            Dictionary mapping deployment names to replica counts
        """
        try:
            import subprocess
            import json

            # Get deployments in JSON format
            result = subprocess.run(
                ['kubectl', 'get', 'deployments', '-n', namespace, '-o', 'json'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                print(f"Warning: Failed to get deployments: {result.stderr}")
                return {}

            deployments_data = json.loads(result.stdout)
            replicas = {}

            for deployment in deployments_data.get('items', []):
                name = deployment['metadata']['name']
                status = deployment.get('status', {})
                replicas[name] = {
                    'desired': status.get('replicas', 0),
                    'ready': status.get('readyReplicas', 0),
                    'available': status.get('availableReplicas', 0)
                }

            return replicas

        except subprocess.SubprocessError as e:
            print(f"Warning: Failed to query deployments: {e}")
            return {}
        except Exception as e:
            print(f"Warning: Error getting deployment replicas: {e}")
            return {}


def correlate_workload_and_metrics(
    workload_results,
    metrics_data: Dict[str, List[Tuple[float, float]]],
    time_offset: float = 0.0
) -> pd.DataFrame:
    """
    Correlate workload generator results with collected metrics.

    Args:
        workload_results: Results from workload_generator
        metrics_data: Metrics from collect_metrics_during_experiment
        time_offset: Time offset to align data (seconds)

    Returns:
        DataFrame with aligned workload and server metrics
    """
    # Create base DataFrame from workload results
    workload_df = pd.DataFrame([
        {
            'timestamp': r.timestamp + time_offset,
            'workload_response_time': r.response_time,
            'workload_success': r.success
        }
        for r in workload_results.requests
    ])

    # Add metrics data
    for metric_name, points in metrics_data.items():
        if not points:
            continue

        metric_df = pd.DataFrame(points, columns=['timestamp', metric_name])

        # Merge with workload data using nearest timestamp
        workload_df = pd.merge_asof(
            workload_df.sort_values('timestamp'),
            metric_df.sort_values('timestamp'),
            on='timestamp',
            direction='nearest'
        )

    return workload_df


# Convenience functions for common use cases
def quick_metrics_snapshot(prometheus_url: str = "http://localhost:9090") -> Dict[str, float]:
    """
    Quick snapshot of current M/M/1 system metrics.

    Args:
        prometheus_url: Prometheus server URL

    Returns:
        Dictionary with current metric values
    """
    collector = PrometheusCollector(prometheus_url)
    try:
        return collector.get_current_metrics()
    finally:
        collector.close()


def monitor_during_workload(
    workload_duration: float,
    prometheus_url: str = "http://localhost:9090"
) -> Dict[str, List[Tuple[float, float]]]:
    """
    Monitor key metrics during a workload generation session.

    Args:
        workload_duration: Duration to monitor (seconds)
        prometheus_url: Prometheus server URL

    Returns:
        Time series data for key metrics
    """
    collector = PrometheusCollector(prometheus_url)
    try:
        return collector.collect_metrics_during_experiment(workload_duration)
    finally:
        collector.close()


# Plotting functions for M/M/1 validation analysis
def plot_mm1_validation_analysis(
    theoretical_predictions: Dict,
    measured_metrics: Dict,
    estimated_mu: float
) -> None:
    """
    Create comprehensive M/M/1 validation plots.

    Args:
        theoretical_predictions: Dictionary with theoretical metrics for each lambda
        measured_metrics: Dictionary with measured metrics for each lambda
        estimated_mu: Estimated service rate
    """
    # Prepare data arrays
    lambda_values = sorted(measured_metrics.keys())
    theoretical_data = {'utilization': [], 'throughput': [], 'response_time': []}
    measured_data = {'utilization': [], 'throughput': [], 'response_time': []}
    utilization_levels = []

    for lambda_rate in lambda_values:
        theory = theoretical_predictions[lambda_rate]
        measured = measured_metrics[lambda_rate]

        theoretical_data['utilization'].append(theory['utilization'])
        theoretical_data['throughput'].append(theory['throughput'])
        theoretical_data['response_time'].append(theory['response_time'])

        measured_data['utilization'].append(measured['utilization'])
        measured_data['throughput'].append(measured['throughput'])
        measured_data['response_time'].append(measured['response_time'])

        utilization_levels.append(theory['utilization'])

    # Create comprehensive validation plots
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle('M/M/1 Model Validation: Theory vs Measurements\\n'
                f'Estimated Service Rate μ = {estimated_mu:.2f} req/s',
                fontsize=14, fontweight='bold')

    # 1. Throughput validation
    axes[0,0].plot(utilization_levels, theoretical_data['throughput'], 'b-', linewidth=2,
                   marker='o', markersize=8, label='Theoretical')
    axes[0,0].plot(utilization_levels, measured_data['throughput'], 'r--', linewidth=2,
                   marker='s', markersize=8, label='Measured')
    axes[0,0].set_xlabel('Utilization (ρ)')
    axes[0,0].set_ylabel('Throughput (req/s)')
    axes[0,0].set_title('Throughput Validation')
    axes[0,0].legend()
    axes[0,0].grid(True, alpha=0.3)

    # 2. Response time validation
    axes[0,1].plot(utilization_levels, theoretical_data['response_time'], 'b-', linewidth=2,
                   marker='o', markersize=8, label='Theoretical')
    axes[0,1].plot(utilization_levels, measured_data['response_time'], 'r--', linewidth=2,
                   marker='s', markersize=8, label='Measured')
    axes[0,1].set_xlabel('Utilization (ρ)')
    axes[0,1].set_ylabel('Response Time (s)')
    axes[0,1].set_title('Response Time Validation')
    axes[0,1].legend()
    axes[0,1].grid(True, alpha=0.3)

    # 3. Correlation analysis
    max_val = max(max(theoretical_data['utilization']), max(measured_data['utilization']))
    axes[1,0].scatter(theoretical_data['utilization'], measured_data['utilization'],
                      color='green', s=100, alpha=0.7, edgecolors='black')
    axes[1,0].plot([0, max_val], [0, max_val], 'k--', alpha=0.5, label='Perfect correlation')
    axes[1,0].set_xlabel('Theoretical Utilization')
    axes[1,0].set_ylabel('Measured Utilization')
    axes[1,0].set_title('Utilization Correlation')
    axes[1,0].legend()
    axes[1,0].grid(True, alpha=0.3)

    # 4. Relative error analysis
    throughput_errors = [(m-t)/t*100 for t, m in zip(theoretical_data['throughput'], measured_data['throughput'])]
    response_time_errors = [(m-t)/t*100 for t, m in zip(theoretical_data['response_time'], measured_data['response_time'])]

    x_pos = np.arange(len(utilization_levels))
    width = 0.35

    bars1 = axes[1,1].bar(x_pos - width/2, throughput_errors, width,
                          label='Throughput Error (%)', alpha=0.7, color='skyblue')
    bars2 = axes[1,1].bar(x_pos + width/2, response_time_errors, width,
                          label='Response Time Error (%)', alpha=0.7, color='lightcoral')

    axes[1,1].set_xlabel('Test Condition')
    axes[1,1].set_ylabel('Relative Error (%)')
    axes[1,1].set_title('Prediction Error Analysis')
    axes[1,1].set_xticks(x_pos)
    axes[1,1].set_xticklabels([f'{u:.0%}' for u in utilization_levels])
    axes[1,1].legend()
    axes[1,1].grid(True, alpha=0.3)
    axes[1,1].axhline(y=0, color='black', linestyle='-', alpha=0.3)

    # Add value labels on error bars
    for bar, error in zip(bars1, throughput_errors):
        height = bar.get_height()
        axes[1,1].text(bar.get_x() + bar.get_width()/2., height + (1 if height >= 0 else -3),
                       f'{error:+.1f}%', ha='center', va='bottom' if height >= 0 else 'top', fontsize=9)

    for bar, error in zip(bars2, response_time_errors):
        height = bar.get_height()
        axes[1,1].text(bar.get_x() + bar.get_width()/2., height + (1 if height >= 0 else -3),
                       f'{error:+.1f}%', ha='center', va='bottom' if height >= 0 else 'top', fontsize=9)

    plt.tight_layout()
    plt.show()


def calculate_validation_statistics(
    theoretical_predictions: Dict,
    measured_metrics: Dict
) -> Dict:
    """
    Calculate statistical validation metrics.

    Args:
        theoretical_predictions: Dictionary with theoretical metrics
        measured_metrics: Dictionary with measured metrics

    Returns:
        Dictionary with validation statistics
    """
    # Prepare data arrays
    lambda_values = sorted(measured_metrics.keys())
    theoretical_data = {'utilization': [], 'throughput': [], 'response_time': []}
    measured_data = {'utilization': [], 'throughput': [], 'response_time': []}

    for lambda_rate in lambda_values:
        theory = theoretical_predictions[lambda_rate]
        measured = measured_metrics[lambda_rate]

        theoretical_data['utilization'].append(theory['utilization'])
        theoretical_data['throughput'].append(theory['throughput'])
        theoretical_data['response_time'].append(theory['response_time'])

        measured_data['utilization'].append(measured['utilization'])
        measured_data['throughput'].append(measured['throughput'])
        measured_data['response_time'].append(measured['response_time'])

    # Calculate correlation coefficients and errors
    stats = {}
    if len(theoretical_data['throughput']) > 1:
        stats['throughput_corr'] = np.corrcoef(theoretical_data['throughput'], measured_data['throughput'])[0,1]
        stats['response_time_corr'] = np.corrcoef(theoretical_data['response_time'], measured_data['response_time'])[0,1]
        stats['utilization_corr'] = np.corrcoef(theoretical_data['utilization'], measured_data['utilization'])[0,1]

        # Calculate relative errors
        throughput_errors = [abs((m-t)/t*100) for t, m in zip(theoretical_data['throughput'], measured_data['throughput'])]
        response_time_errors = [abs((m-t)/t*100) for t, m in zip(theoretical_data['response_time'], measured_data['response_time'])]

        stats['throughput_mae'] = np.mean(throughput_errors)
        stats['response_time_mae'] = np.mean(response_time_errors)

        # Overall assessment
        min_correlation = min(stats['throughput_corr'], stats['response_time_corr'], stats['utilization_corr'])
        max_error = max(stats['throughput_mae'], stats['response_time_mae'])

        if min_correlation > 0.95 and max_error < 10:
            assessment = "Excellent"
        elif min_correlation > 0.90 and max_error < 20:
            assessment = "Good"
        elif min_correlation > 0.75 and max_error < 30:
            assessment = "Acceptable"
        else:
            assessment = "Poor"

        stats['assessment'] = assessment
        stats['min_correlation'] = min_correlation
        stats['max_error'] = max_error
    else:
        stats['error'] = "Insufficient data points for statistical analysis"

    return stats


if __name__ == "__main__":
    # Example usage
    print("Testing Prometheus collector...")

    collector = PrometheusCollector()

    # Health check
    if not collector.health_check():
        print("❌ Prometheus not accessible")
        exit(1)

    print("✅ Prometheus is healthy")

    # Get current metrics
    metrics = collector.get_current_metrics()
    print("\nCurrent metrics:")
    for name, value in metrics.items():
        if value is not None:
            print(f"  {name}: {value:.3f}")
        else:
            print(f"  {name}: No data")

    collector.close()