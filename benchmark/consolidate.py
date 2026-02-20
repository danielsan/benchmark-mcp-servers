#!/usr/bin/env python3
"""
Consolidates benchmark results from all servers into a single summary.json.
Usage: python3 consolidate.py <results_dir>
"""

import json
import sys
import os
from datetime import datetime, timezone

SERVERS = ['python', 'go', 'nodejs', 'java', 'rust']


def load_json(path):
    """Load JSON file, return None if not found."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Warning: Could not load {path}: {e}")
        return None


def consolidate(results_dir):
    """Build summary from all server results."""
    summary = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'results_dir': results_dir,
        'config': {
            'vus': 50,
            'duration': '5m',
            'cpu_limit': '1.0',
            'memory_limit': '1G',
        },
        'servers': {},
    }

    for server in SERVERS:
        server_dir = os.path.join(results_dir, server)
        k6_data = load_json(os.path.join(server_dir, 'k6.json'))
        stats_data = load_json(os.path.join(server_dir, 'stats.json'))

        if not k6_data:
            print(f"Skipping {server} — no k6 data")
            continue

        entry = {
            'http': k6_data.get('http', {}),
            'mcp': k6_data.get('mcp', {}),
            'tools': k6_data.get('tools', {}),
        }

        if stats_data and 'summary' in stats_data:
            entry['resources'] = stats_data['summary']

        summary['servers'][server] = entry

    # Ranking
    if summary['servers']:
        rankings = {}

        # Rank by avg latency (lower is better)
        latencies = {
            name: data['http'].get('latency', {}).get('avg', float('inf'))
            for name, data in summary['servers'].items()
        }
        rankings['latency_avg'] = sorted(latencies, key=latencies.get)

        # Rank by p95 latency (lower is better)
        p95s = {
            name: data['http'].get('latency', {}).get('p95', float('inf'))
            for name, data in summary['servers'].items()
        }
        rankings['latency_p95'] = sorted(p95s, key=p95s.get)

        # Rank by RPS (higher is better)
        rps_vals = {
            name: data['http'].get('rps', 0)
            for name, data in summary['servers'].items()
        }
        rankings['rps'] = sorted(rps_vals, key=rps_vals.get, reverse=True)

        # Rank by avg CPU usage (lower is better)
        cpus = {}
        for name, data in summary['servers'].items():
            cpu_avg = data.get('resources', {}).get('cpu', {}).get('avg')
            if cpu_avg is not None:
                cpus[name] = cpu_avg
        if cpus:
            rankings['cpu_efficiency'] = sorted(cpus, key=cpus.get)

        # Rank by avg memory (lower is better)
        mems = {}
        for name, data in summary['servers'].items():
            mem_avg = data.get('resources', {}).get('memory_mb', {}).get('avg')
            if mem_avg is not None:
                mems[name] = mem_avg
        if mems:
            rankings['memory_efficiency'] = sorted(mems, key=mems.get)

        summary['rankings'] = rankings

    return summary


def print_summary(summary):
    """Print a readable summary table."""
    servers = summary.get('servers', {})
    if not servers:
        print("No results to display.")
        return

    print("\n" + "=" * 75)
    print("  BENCHMARK SUMMARY")
    print("=" * 75)

    # Header
    print(f"\n  {'Server':<10} {'RPS':>8} {'Avg(ms)':>8} {'P50(ms)':>8} {'P95(ms)':>8} {'P99(ms)':>8} {'CPU%':>7} {'MEM(MB)':>8}")
    print("  " + "-" * 67)

    for name in ['python', 'go', 'nodejs', 'java', 'rust']:
        data = servers.get(name)
        if not data:
            continue
        lat = data.get('http', {}).get('latency', {})
        rps = data.get('http', {}).get('rps', 0)
        cpu = data.get('resources', {}).get('cpu', {}).get('avg', 0)
        mem = data.get('resources', {}).get('memory_mb', {}).get('avg', 0)

        print(f"  {name:<10} {rps:>8.1f} {lat.get('avg', 0):>8.1f} {lat.get('p50', 0):>8.1f} {lat.get('p95', 0):>8.1f} {lat.get('p99', 0):>8.1f} {cpu:>7.1f} {mem:>8.1f}")

    # Rankings
    rankings = summary.get('rankings', {})
    if rankings:
        print(f"\n  {'Rankings':}")
        print("  " + "-" * 40)
        labels = {
            'latency_avg': 'Best Avg Latency',
            'latency_p95': 'Best P95 Latency',
            'rps': 'Highest RPS',
            'cpu_efficiency': 'Most CPU Efficient',
            'memory_efficiency': 'Most Memory Efficient',
        }
        for key, label in labels.items():
            if key in rankings:
                order = rankings[key]
                print(f"  {label:<25} 🥇 {order[0]}", end="")
                if len(order) > 1:
                    print(f"  🥈 {order[1]}", end="")
                if len(order) > 2:
                    print(f"  🥉 {order[2]}", end="")
                print()

    print("\n" + "=" * 75)


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <results_dir>")
        sys.exit(1)

    results_dir = sys.argv[1]
    summary = consolidate(results_dir)

    output_path = os.path.join(results_dir, 'summary.json')
    with open(output_path, 'w') as f:
        json.dump(summary, f, indent=2)

    print(f"Summary saved to {output_path}")
    print_summary(summary)


if __name__ == '__main__':
    main()
