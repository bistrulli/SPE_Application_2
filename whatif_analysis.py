#!/usr/bin/env python3
"""
What-If Analysis Wrapper for Kubernetes Performance Testing

This script runs multiple experiments with different numbers of users,
collects both measured metrics and JMT predictions, and generates
comparison plots showing measured vs predicted performance.

Usage:
    python whatif_analysis.py \
        --users-range 10,20,30,50,100 \
        --manifest QNonK8s/applications/3tier.yaml \
        --config experiment_mapping.yaml \
        --think-time 0.5 \
        --duration 300
"""

import argparse
import subprocess
import sys
import yaml
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
from datetime import datetime
from typing import List, Dict
import time
import re
import signal
import glob


class WhatIfAnalysis:
    """
    Wrapper for running what-if analysis experiments with varying user loads.
    """

    def __init__(
        self,
        users_range: List[int],
        manifest: str,
        config: str,
        think_time: float,
        duration: int,
        mode: str = "constant",
        output_dir: str = "results",
        cooldown: int = 10
    ):
        """
        Initialize what-if analysis.

        Args:
            users_range: List of user counts to test
            manifest: Path to K8s manifest
            config: Path to experiment_mapping.yaml
            think_time: Fixed think time for all experiments
            duration: Fixed duration for all experiments
            mode: Test mode (constant or trace)
            output_dir: Base output directory
            cooldown: Seconds to wait between experiments (default: 10)
        """
        self.users_range = sorted(users_range)
        self.manifest = manifest
        self.config = config
        self.think_time = think_time
        self.duration = duration
        self.mode = mode
        self.base_output_dir = Path(output_dir)
        self.cooldown = cooldown

        # Track currently running experiment process
        self.current_process = None

        # Create timestamped output directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = self.base_output_dir / f"whatif_{timestamp}"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Load experiment mapping to identify tasks
        self.tasks = self._load_tasks_from_config()

        # Storage for aggregated results
        self.results = {
            'users': []
        }

        print(f"\n{'='*60}")
        print(f"🔬 What-If Analysis Configuration")
        print(f"{'='*60}")
        print(f"Users range: {self.users_range}")
        print(f"Think time: {self.think_time}s")
        print(f"Duration: {self.duration}s")
        print(f"Tasks detected: {list(self.tasks.keys())}")
        print(f"Output directory: {self.output_dir}")
        print(f"{'='*60}\n")

    def _load_tasks_from_config(self) -> Dict[str, Dict]:
        """
        Load task information from experiment_mapping.yaml.

        Returns:
            Dictionary mapping task names to their config
        """
        try:
            with open(self.config, 'r') as f:
                config = yaml.safe_load(f)

            tasks = {}
            mapping = config.get('mapping', {})

            for deployment_key, deployment_config in mapping.items():
                if deployment_key == 'delay':
                    continue

                task_name = deployment_key.replace('-deployment', '')
                tasks[task_name] = {
                    'deployment': deployment_key,
                    'jmt_station': deployment_config.get('jmt_station', '')
                }

            return tasks

        except Exception as e:
            print(f"⚠️  Error loading config: {e}")
            return {}

    def run_experiment(self, users: int) -> Dict:
        """
        Run a single experiment with specified number of users.

        Args:
            users: Number of users for this experiment

        Returns:
            Dictionary with experiment results
        """
        print(f"\n{'='*60}")
        print(f"🚀 Running experiment with {users} users")
        print(f"{'='*60}")

        # Build command
        cmd = [
            'python3', 'experiment_runner.py',
            '--manifest', self.manifest,
            '--config', self.config,
            '--mode', self.mode,
            '--users', str(users),
            '--think-time', str(self.think_time),
            '--duration', str(self.duration)
        ]

        print(f"Command: {' '.join(cmd)}\n")

        try:
            # Run experiment with real-time output
            self.current_process = subprocess.Popen(
                cmd,
                stdout=None,  # Output goes directly to terminal
                stderr=None,
                text=True
            )

            # Wait for completion
            returncode = self.current_process.wait(timeout=self.duration + 600)
            self.current_process = None

            if returncode != 0:
                print(f"\n❌ Experiment failed with return code {returncode}")
                return {}

            print(f"\n✅ Experiment completed successfully")

            # Find the experiment output directory (most recent timestamp)
            experiment_id = self._find_latest_experiment_id()
            if not experiment_id:
                print("⚠️  Could not find experiment output directory")
                return {}

            # Read results
            experiment_dir = self.base_output_dir / experiment_id
            results = self._read_experiment_results(experiment_dir)

            return results

        except subprocess.TimeoutExpired:
            print(f"\n❌ Experiment timed out")
            if self.current_process:
                self.current_process.kill()
                self.current_process = None
            return {}
        except Exception as e:
            print(f"\n❌ Error running experiment: {e}")
            if self.current_process:
                self.current_process.kill()
                self.current_process = None
            import traceback
            traceback.print_exc()
            return {}

    def _find_latest_experiment_id(self) -> str:
        """
        Find the most recent experiment ID from the results directory.

        Returns:
            Experiment ID or empty string
        """
        # Look for timestamp directories in results/ (format: YYYYMMDD_HHMMSS)
        timestamp_pattern = "[0-9]" * 8 + "_" + "[0-9]" * 6  # "YYYYMMDD_HHMMSS"
        pattern = str(self.base_output_dir / timestamp_pattern)
        matching_dirs = glob.glob(pattern)

        if not matching_dirs:
            return ""

        # Get the most recent one (lexicographically sorted = chronologically sorted)
        latest_dir = Path(max(matching_dirs))
        return latest_dir.name

    def _read_experiment_results(self, experiment_dir: Path) -> Dict:
        """
        Read experiment results from summary statistics CSV.

        Args:
            experiment_dir: Path to experiment output directory

        Returns:
            Dictionary with measured and JMT predicted metrics
        """
        results = {}

        # Map JMT station names to task names and metric display names to technical names
        station_to_task_map = {task_info['jmt_station']: task_name
                               for task_name, task_info in self.tasks.items()}

        metric_name_map = {
            'Throughput': 'throughput',
            'Response Time': 'response_time',
            'CPU Utilization': 'utilization',
        }

        # Read summary statistics (measured metrics)
        summary_file = experiment_dir / "summary_statistics.csv"
        if summary_file.exists():
            df = pd.read_csv(summary_file)

            # Extract mean values for each metric, normalizing keys
            for _, row in df.iterrows():
                metric_display_name = row['Metric']
                mean_value = row['Mean']

                # Try to normalize the key to task_metric format
                # Example: "Tier1 Throughput" → "task1_throughput_measured"
                normalized = False
                for station_name, task_name in station_to_task_map.items():
                    for display_metric, tech_metric in metric_name_map.items():
                        if station_name in metric_display_name and display_metric in metric_display_name:
                            key = f"{task_name}_{tech_metric}_measured"
                            results[key] = mean_value
                            normalized = True
                            break
                    if normalized:
                        break

                # Also store with original name for other metrics (Locust, etc.)
                if not normalized:
                    results[f"{metric_display_name}_measured"] = mean_value
        else:
            print(f"⚠️  Summary file not found: {summary_file}")

        # Read JMT results if available
        jmt_results_file = experiment_dir / "jmt_validation_results.csv"
        if jmt_results_file.exists():
            df_jmt = pd.read_csv(jmt_results_file)

            # Extract JMT predictions and errors
            for _, row in df_jmt.iterrows():
                station = row['station']
                metric = row['metric']
                jmt_value = row['jmt_predicted']
                error_pct = row['error_pct']

                # Map station to task name
                task_name = self._station_to_task(station)
                if task_name:
                    key = f"{task_name}_{metric}_jmt"
                    results[key] = jmt_value

                    # Also store error percentage for boxplot
                    error_key = f"{task_name}_{metric}_error_pct"
                    results[error_key] = error_pct
        else:
            print(f"⚠️  JMT results not found: {jmt_results_file}")

        return results

    def _station_to_task(self, station_name: str) -> str:
        """
        Map JMT station name to task name.

        Args:
            station_name: JMT station name (e.g., "Tier1")

        Returns:
            Task name (e.g., "task1") or empty string
        """
        for task_name, task_config in self.tasks.items():
            if task_config['jmt_station'] == station_name:
                return task_name
        return ""

    def _cooldown(self, message: str = "Cooldown"):
        """
        Wait for cooldown period with visual countdown feedback.

        Args:
            message: Message to display during cooldown
        """
        if self.cooldown <= 0:
            return

        print(f"\n⏳ {message} - waiting {self.cooldown}s...")
        for remaining in range(self.cooldown, 0, -1):
            print(f"  ⏱️  {remaining}s remaining...", end='\r', flush=True)
            time.sleep(1)
        print("  ✅ Ready!                    ")

    def run_all_experiments(self):
        """
        Run all experiments with different user counts.
        """
        successful_runs = 0
        total_runs = len(self.users_range)

        try:
            for i, users in enumerate(self.users_range, 1):
                # Cooldown before each experiment (including the first)
                if i == 1:
                    self._cooldown("Cluster initialization")
                else:
                    self._cooldown("Preparing next experiment")

                print(f"\n{'#'*60}")
                print(f"# Experiment {i}/{total_runs}: {users} users")
                print(f"{'#'*60}")

                results = self.run_experiment(users)

                if results:
                    # Store results
                    self.results['users'].append(users)
                    for key, value in results.items():
                        if key not in self.results:
                            self.results[key] = []
                        self.results[key].append(value)

                    successful_runs += 1
                else:
                    print(f"⚠️  Skipping failed experiment")

        except KeyboardInterrupt:
            print(f"\n\n{'='*60}")
            print(f"⚠️  USER INTERRUPT - Cleanup in progress...")
            print(f"{'='*60}")

            # Send SIGINT to current experiment_runner process
            if self.current_process:
                print(f"  Sending SIGINT to experiment_runner process...")
                try:
                    self.current_process.send_signal(signal.SIGINT)
                    print(f"  Waiting for termination (max 30s)...")
                    self.current_process.wait(timeout=30)
                    print(f"  ✅ Process terminated successfully")
                except subprocess.TimeoutExpired:
                    print(f"  ⚠️  Timeout - killing process...")
                    self.current_process.kill()
                    self.current_process.wait()
                except Exception as e:
                    print(f"  ⚠️  Error during cleanup: {e}")

            print(f"\n{'='*60}")
            print(f"📊 Experiments completed before interruption: {successful_runs}/{total_runs}")
            print(f"{'='*60}")
            raise

        print(f"\n{'='*60}")
        print(f"✅ Completed {successful_runs}/{total_runs} experiments successfully")
        print(f"{'='*60}\n")

    def save_aggregated_results(self):
        """
        Save all results to aggregated CSV file.
        """
        output_file = self.output_dir / "aggregated_results.csv"

        df = pd.DataFrame(self.results)
        df.to_csv(output_file, index=False)

        print(f"📊 Saved aggregated results to: {output_file}")

    def generate_plots(self):
        """
        Generate comparison plots for each task and metric.
        """
        print(f"\n{'='*60}")
        print(f"📈 Generating comparison plots")
        print(f"{'='*60}\n")

        metrics = ['throughput', 'response_time', 'cpu_usage', 'utilization']

        for task_name in self.tasks.keys():
            for metric in metrics:
                self._plot_metric(task_name, metric)

        print(f"\n✅ All plots generated in: {self.output_dir}")

    def _plot_metric(self, task_name: str, metric: str):
        """
        Create a comparison plot for a specific task and metric.

        Args:
            task_name: Name of the task (e.g., "task1")
            metric: Name of the metric (e.g., "throughput")
        """
        # Build unified metric keys: task_name_metric_measured and task_name_metric_jmt
        measured_key = f"{task_name}_{metric}_measured"
        jmt_key = f"{task_name}_{metric}_jmt"

        # Skip if no data available for either measured or JMT
        if measured_key not in self.results and jmt_key not in self.results:
            return

        # Create plot
        fig, ax = plt.subplots(figsize=(10, 6))

        users = self.results['users']

        # Plot measured values if available
        if measured_key in self.results:
            measured_values = self.results[measured_key]
            ax.plot(users, measured_values,
                   marker='o', linestyle='-', linewidth=2, markersize=8,
                   color='blue', label='Measured')

        # Plot JMT predicted values if available
        if jmt_key in self.results:
            jmt_values = self.results[jmt_key]
            ax.plot(users, jmt_values,
                   marker='s', linestyle='--', linewidth=2, markersize=8,
                   color='red', label='JMT Predicted')

        # Formatting
        ax.set_xlabel('Number of Users', fontsize=12, fontweight='bold')

        # Set Y label with appropriate units
        ylabel, unit = self._get_metric_label_and_unit(metric)
        ax.set_ylabel(f"{ylabel} ({unit})", fontsize=12, fontweight='bold')

        ax.set_title(f"{task_name.upper()} - {ylabel}", fontsize=14, fontweight='bold')
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)

        # Save plot
        output_file = self.output_dir / f"{task_name}_{metric}.png"
        plt.tight_layout()
        plt.savefig(output_file, dpi=150)
        plt.close()

        print(f"  ✓ Generated: {task_name}_{metric}.png")

    def _get_metric_label_and_unit(self, metric: str) -> tuple:
        """
        Get human-readable label and unit for a metric.

        Args:
            metric: Metric name

        Returns:
            Tuple of (label, unit)
        """
        metric_info = {
            'throughput': ('Throughput', 'req/s'),
            'response_time': ('Response Time', 's'),
            'cpu_usage': ('CPU Usage', 'cores'),
            'utilization': ('Utilization', 'ratio'),
        }

        return metric_info.get(metric, (metric.replace('_', ' ').title(), ''))

    def generate_error_boxplot(self):
        """
        Generate a box plot showing error distribution by metric type.
        Aggregates errors from all tasks and all user load levels.
        """
        print(f"\n{'='*60}")
        print(f"📊 Generating error distribution box plot")
        print(f"{'='*60}\n")

        # Collect errors by metric type
        error_data = {
            'throughput': [],
            'response_time': [],
            'utilization': []
        }

        # Aggregate errors from all experiments (all users, all tasks)
        for metric_type in error_data.keys():
            for task_name in self.tasks.keys():
                error_key = f"{task_name}_{metric_type}_error_pct"
                if error_key in self.results:
                    # self.results[error_key] is a list of errors for each user count
                    error_data[metric_type].extend(self.results[error_key])

        # Filter out metric types with no data
        error_data = {k: v for k, v in error_data.items() if v}

        if not error_data:
            print("⚠️  No error data available for box plot")
            return

        # Create box plot
        fig, ax = plt.subplots(figsize=(10, 6))

        # Prepare data for boxplot
        labels = []
        data_to_plot = []
        for metric_type in ['throughput', 'response_time', 'utilization']:
            if metric_type in error_data and error_data[metric_type]:
                labels.append(metric_type.replace('_', ' ').title())
                data_to_plot.append(error_data[metric_type])

        if not data_to_plot:
            print("⚠️  No data to plot")
            return

        # Create box plot with custom colors
        bp = ax.boxplot(data_to_plot, labels=labels, patch_artist=True)

        # Customize colors
        colors = ['#FF6B6B', '#4ECDC4', '#45B7D1']
        for patch, color in zip(bp['boxes'], colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.7)

        # Add median labels
        for i, line in enumerate(bp['medians']):
            x, y = line.get_xydata()[1]
            median_val = data_to_plot[i][len(data_to_plot[i])//2] if data_to_plot[i] else 0
            median_val = float(line.get_ydata()[0])
            ax.text(x + 0.1, y, f'{median_val:.1f}%', verticalalignment='center', fontsize=10)

        # Formatting
        ax.set_ylabel('Error Percentage (%)', fontsize=12, fontweight='bold')
        ax.set_xlabel('Metric Type', fontsize=12, fontweight='bold')
        ax.set_title('Prediction Error Distribution by Metric Type\n(Aggregated across all tasks and load levels)',
                    fontsize=14, fontweight='bold')
        ax.grid(True, alpha=0.3, axis='y')

        # Add horizontal line at 15% threshold
        ax.axhline(y=15, color='red', linestyle='--', linewidth=2,
                   label='Acceptable threshold (15%)')
        ax.legend(loc='upper right')

        # Save plot
        output_file = self.output_dir / "error_distribution.png"
        plt.tight_layout()
        plt.savefig(output_file, dpi=150)
        plt.close()

        print(f"  ✅ Generated: error_distribution.png")

        # Print summary statistics
        print(f"\n📈 Error Statistics:")
        for metric_type, errors in error_data.items():
            if errors:
                import statistics
                print(f"  {metric_type.replace('_', ' ').title()}:")
                print(f"    Mean: {statistics.mean(errors):.2f}%")
                print(f"    Median: {statistics.median(errors):.2f}%")
                print(f"    Std Dev: {statistics.stdev(errors) if len(errors) > 1 else 0:.2f}%")
                print(f"    Min: {min(errors):.2f}%")
                print(f"    Max: {max(errors):.2f}%")


def main():
    """
    Main entry point for what-if analysis.
    """
    parser = argparse.ArgumentParser(
        description='What-If Analysis Wrapper for Kubernetes Performance Testing',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    # Required arguments
    parser.add_argument('--users-range', required=True,
                        help='Comma-separated list of user counts to test (e.g., 10,20,50,100)')
    parser.add_argument('--manifest', required=True,
                        help='Path to Kubernetes manifest YAML')
    parser.add_argument('--config', required=True,
                        help='Path to experiment_mapping.yaml')

    # Optional arguments
    parser.add_argument('--think-time', type=float, default=0.5,
                        help='Think time between requests in seconds (default: 0.5)')
    parser.add_argument('--duration', type=int, default=300,
                        help='Test duration in seconds (default: 300)')
    parser.add_argument('--mode', default='constant', choices=['constant', 'trace'],
                        help='Load testing mode (default: constant)')
    parser.add_argument('--output-dir', default='results',
                        help='Base output directory (default: results)')
    parser.add_argument('--cooldown', type=int, default=10,
                        help='Seconds to wait between experiments (default: 10)')

    args = parser.parse_args()

    # Parse users range
    try:
        users_range = [int(x.strip()) for x in args.users_range.split(',')]
        if not users_range:
            print("❌ Error: users-range must contain at least one value")
            sys.exit(1)
    except ValueError:
        print("❌ Error: users-range must be comma-separated integers (e.g., 10,20,50,100)")
        sys.exit(1)

    # Create and run analysis
    analysis = WhatIfAnalysis(
        users_range=users_range,
        manifest=args.manifest,
        config=args.config,
        think_time=args.think_time,
        duration=args.duration,
        mode=args.mode,
        output_dir=args.output_dir,
        cooldown=args.cooldown
    )

    # Run all experiments
    analysis.run_all_experiments()

    # Save aggregated results
    analysis.save_aggregated_results()

    # Generate plots
    analysis.generate_plots()

    # Generate error distribution box plot
    analysis.generate_error_boxplot()

    print(f"\n{'='*60}")
    print(f"🎉 What-If Analysis Complete!")
    print(f"{'='*60}")
    print(f"Results saved in: {analysis.output_dir}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
