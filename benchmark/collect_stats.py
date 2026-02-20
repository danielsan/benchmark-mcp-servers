#!/usr/bin/env python3
"""
Docker Stats Collector — polls container resource usage via Docker API.
Runs in background during benchmark, saves JSON output.

Usage: python3 collect_stats.py <container_name> <output_file> [poll_interval_s]
"""

import sys
import os
import json
import time
import signal
import urllib.request
import urllib.error
import http.client
import socket
from datetime import datetime, timezone


DOCKER_SOCKET_PATHS = [
    '/var/run/docker.sock',
    os.path.expanduser('~/.docker/run/docker.sock'),
    os.path.expanduser('~/.docker/desktop/docker.sock'),
]


def find_docker_socket():
    for path in DOCKER_SOCKET_PATHS:
        if os.path.exists(path):
            return path
    return DOCKER_SOCKET_PATHS[0]  # fall back to default so the error is descriptive


class DockerStatsCollector:
    """Collects container stats via Docker Engine API over Unix socket."""

    def __init__(self, container_name, output_file, poll_interval=1.0):
        self.container_name = container_name
        self.output_file = output_file
        self.poll_interval = poll_interval
        self.samples = []
        self.running = True
        self.docker_socket = find_docker_socket()
        self._last_error = None  # suppress repeated identical errors

    def _docker_api_get(self, path):
        """Make a GET request to Docker Engine API via Unix socket."""
        conn = http.client.HTTPConnection('localhost')
        conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.sock.connect(self.docker_socket)
        conn.request('GET', path)
        response = conn.getresponse()
        data = response.read().decode('utf-8')
        conn.close()
        return json.loads(data)

    def _calculate_cpu_percent(self, stats):
        """Calculate CPU usage percentage from Docker stats."""
        cpu_delta = (
            stats['cpu_stats']['cpu_usage']['total_usage'] -
            stats['precpu_stats']['cpu_usage']['total_usage']
        )
        system_delta = (
            stats['cpu_stats']['system_cpu_usage'] -
            stats['precpu_stats']['system_cpu_usage']
        )
        num_cpus = stats['cpu_stats'].get('online_cpus', 1)

        if system_delta > 0 and cpu_delta >= 0:
            return (cpu_delta / system_delta) * num_cpus * 100.0
        return 0.0

    def _get_memory_mb(self, stats):
        """Extract memory usage in MB from Docker stats."""
        mem = stats.get('memory_stats', {})
        usage = mem.get('usage', 0)
        cache = mem.get('stats', {}).get('cache', 0)
        return (usage - cache) / (1024 * 1024)

    def _get_memory_limit_mb(self, stats):
        """Extract memory limit in MB."""
        return stats.get('memory_stats', {}).get('limit', 0) / (1024 * 1024)

    def _get_network_io(self, stats):
        """Extract network I/O bytes."""
        networks = stats.get('networks', {})
        rx = sum(n.get('rx_bytes', 0) for n in networks.values())
        tx = sum(n.get('tx_bytes', 0) for n in networks.values())
        return rx, tx

    def collect_sample(self):
        """Collect a single stats sample."""
        try:
            stats = self._docker_api_get(
                f'/containers/{self.container_name}/stats?stream=false'
            )

            cpu_percent = self._calculate_cpu_percent(stats)
            memory_mb = self._get_memory_mb(stats)
            memory_limit_mb = self._get_memory_limit_mb(stats)
            memory_percent = (memory_mb / memory_limit_mb * 100) if memory_limit_mb > 0 else 0
            net_rx, net_tx = self._get_network_io(stats)

            sample = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'cpu_percent': round(cpu_percent, 2),
                'memory_mb': round(memory_mb, 2),
                'memory_percent': round(memory_percent, 2),
                'memory_limit_mb': round(memory_limit_mb, 2),
                'net_rx_bytes': net_rx,
                'net_tx_bytes': net_tx,
            }
            self.samples.append(sample)
            return sample
        except Exception as e:
            msg = str(e)
            if msg != self._last_error:
                print(f"Error collecting stats: {e}", file=sys.stderr)
                self._last_error = msg
            return None

    def save(self):
        """Save collected samples to JSON file."""
        output = {
            'container': self.container_name,
            'poll_interval_s': self.poll_interval,
            'sample_count': len(self.samples),
            'samples': self.samples,
        }

        if self.samples:
            cpu_values = [s['cpu_percent'] for s in self.samples]
            mem_values = [s['memory_mb'] for s in self.samples]
            output['summary'] = {
                'cpu': {
                    'avg': round(sum(cpu_values) / len(cpu_values), 2),
                    'max': round(max(cpu_values), 2),
                    'min': round(min(cpu_values), 2),
                },
                'memory_mb': {
                    'avg': round(sum(mem_values) / len(mem_values), 2),
                    'max': round(max(mem_values), 2),
                    'min': round(min(mem_values), 2),
                },
            }

        with open(self.output_file, 'w') as f:
            json.dump(output, f, indent=2)

        print(f"Saved {len(self.samples)} samples to {self.output_file}")

    def run(self):
        """Main collection loop."""
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, 'running', False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, 'running', False))

        print(f"Collecting stats for {self.container_name} → {self.output_file}")
        print(f"Poll interval: {self.poll_interval}s. Docker socket: {self.docker_socket}")
        print(f"Send SIGTERM/SIGINT to stop.")

        while self.running:
            sample = self.collect_sample()
            if sample:
                cpu = sample['cpu_percent']
                mem = sample['memory_mb']
                print(f"  CPU: {cpu:6.1f}%  MEM: {mem:7.1f}MB", end='\r')
            time.sleep(self.poll_interval)

        print()  # newline after \r
        self.save()


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <container_name> <output_file> [poll_interval_s]")
        sys.exit(1)

    container = sys.argv[1]
    output = sys.argv[2]
    interval = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0

    collector = DockerStatsCollector(container, output, interval)
    collector.run()
