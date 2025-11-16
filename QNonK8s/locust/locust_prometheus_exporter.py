"""
Prometheus exporter for Locust metrics.

This module provides a Prometheus exporter that exposes key Locust performance
metrics including active users, request rate, response times, and failure rates.
"""

import logging
from prometheus_client import Gauge, start_http_server
from locust import events

logger = logging.getLogger(__name__)

# Prometheus metrics
locust_users = Gauge('locust_users', 'Number of active Locust users')
locust_requests_per_second = Gauge('locust_requests_per_second', 'Current request rate (req/s)')
locust_response_time_avg = Gauge('locust_response_time_avg', 'Average response time in milliseconds')
locust_response_time_p50 = Gauge('locust_response_time_p50', '50th percentile response time')
locust_response_time_p95 = Gauge('locust_response_time_p95', '95th percentile response time')
locust_response_time_p99 = Gauge('locust_response_time_p99', '99th percentile response time')
locust_failure_rate = Gauge('locust_failure_rate', 'Request failure rate (0-1)')
locust_total_requests = Gauge('locust_total_requests', 'Total number of requests')
locust_total_failures = Gauge('locust_total_failures', 'Total number of failed requests')


class LocustPrometheusExporter:
    """
    Prometheus exporter for Locust load testing metrics.

    Automatically collects and exposes Locust statistics to Prometheus.
    """

    def __init__(self, port: int = 9646):
        """
        Initialize the Prometheus exporter.

        Args:
            port: Port to expose metrics on (default: 9646)
        """
        self.port = port
        self.exporter_started = False

    def start(self):
        """Start the Prometheus HTTP server and register event handlers."""
        if not self.exporter_started:
            try:
                start_http_server(self.port)
                logger.info(f"Locust Prometheus exporter started on port {self.port}")
                self.exporter_started = True
            except Exception as e:
                logger.error(f"Failed to start Prometheus exporter: {e}")
                raise

        # Register event handlers to update metrics
        events.report_to_master.add_listener(self._on_report_to_master)

    def _on_report_to_master(self, client_id, data, **kwargs):
        """
        Event handler called when workers report to master (or on standalone).
        Updates Prometheus metrics with current Locust statistics.
        """
        # This event is fired regularly with current stats
        # In standalone mode, it still fires with local stats
        pass  # Metrics are updated via poll_stats

    @staticmethod
    def update_metrics_from_environment(environment):
        """
        Manually update Prometheus metrics from Locust environment.
        Call this periodically to push current stats to Prometheus.

        Args:
            environment: Locust Environment object
        """
        try:
            stats = environment.stats
            runner = environment.runner

            # Update user count
            if runner:
                locust_users.set(runner.user_count)

            # Update request statistics
            total_stats = stats.total

            if total_stats.num_requests > 0:
                # Request rate
                locust_requests_per_second.set(total_stats.current_rps or 0)

                # Response times
                locust_response_time_avg.set(total_stats.avg_response_time or 0)
                locust_response_time_p50.set(total_stats.get_response_time_percentile(0.5) or 0)
                locust_response_time_p95.set(total_stats.get_response_time_percentile(0.95) or 0)
                locust_response_time_p99.set(total_stats.get_response_time_percentile(0.99) or 0)

                # Failure rate
                if total_stats.num_requests > 0:
                    failure_rate = total_stats.num_failures / total_stats.num_requests
                    locust_failure_rate.set(failure_rate)
                else:
                    locust_failure_rate.set(0)

                # Totals
                locust_total_requests.set(total_stats.num_requests)
                locust_total_failures.set(total_stats.num_failures)

        except Exception as e:
            logger.error(f"Error updating Prometheus metrics: {e}")


def init_exporter(port: int = 9646):
    """
    Initialize and start the Locust Prometheus exporter.

    Args:
        port: Port to expose metrics on (default: 9646)

    Returns:
        LocustPrometheusExporter instance
    """
    exporter = LocustPrometheusExporter(port=port)
    exporter.start()
    return exporter


# Event handlers for automatic metric updates
@events.init.add_listener
def on_locust_init(environment, **kwargs):
    """
    Initialize Prometheus exporter when Locust starts.
    This is called automatically by Locust.
    """
    # Only initialize if not already done
    if not hasattr(environment, 'prometheus_exporter'):
        exporter_port = int(environment.parsed_options.prometheus_port) if hasattr(environment, 'parsed_options') and hasattr(environment.parsed_options, 'prometheus_port') else 9646
        environment.prometheus_exporter = init_exporter(port=exporter_port)

        # Start a background task to update metrics periodically
        import gevent
        def update_metrics():
            while True:
                LocustPrometheusExporter.update_metrics_from_environment(environment)
                gevent.sleep(1)  # Update every second

        gevent.spawn(update_metrics)
        logger.info("Prometheus exporter initialized and background updater started")


# CLI argument for custom port
@events.init_command_line_parser.add_listener
def on_init_parser(parser):
    """Add custom command-line argument for Prometheus exporter port."""
    parser.add_argument(
        "--prometheus-port",
        type=int,
        default=9646,
        help="Port for Prometheus exporter (default: 9646)"
    )
