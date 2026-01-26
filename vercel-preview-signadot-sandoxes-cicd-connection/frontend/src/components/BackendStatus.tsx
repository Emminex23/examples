'use client';

import { useState, useEffect } from 'react';
import { getApiUrl, getApiHeaders } from '@/lib/config/api';

/**
 * Backend status indicator component
 * Polls health endpoint every 30 seconds
 */
export default function BackendStatus() {
  const [status, setStatus] = useState<'checking' | 'online' | 'offline'>('checking');
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const checkBackendStatus = async () => {
    try {
      setStatus('checking');
      setError(null);

      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 5000);

      const response = await fetch(getApiUrl('/health'), {
        method: 'GET',
        headers: getApiHeaders(),
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      if (response.ok) {
        const data = await response.json();
        if (data.status === 'healthy') {
          setStatus('online');
          setLastChecked(new Date());
        } else {
          setStatus('offline');
          setError('Backend returned unhealthy status');
        }
      } else {
        setStatus('offline');
        setError(`HTTP ${response.status}: ${response.statusText}`);
      }
    } catch (err) {
      setStatus('offline');
      if (err instanceof Error) {
        if (err.name === 'AbortError') {
          setError('Request timeout');
        } else {
          setError(err.message);
        }
      } else {
        setError('Failed to connect to backend');
      }
    }
  };

  useEffect(() => {
    checkBackendStatus();
    const interval = setInterval(() => {
      checkBackendStatus();
    }, 30000);

    return () => clearInterval(interval);
  }, []);

  const getStatusColor = () => {
    switch (status) {
      case 'online':
        return 'bg-green-500';
      case 'offline':
        return 'bg-red-500';
      case 'checking':
        return 'bg-yellow-500';
      default:
        return 'bg-gray-500';
    }
  };

  const getStatusText = () => {
    switch (status) {
      case 'online':
        return 'Backend Online';
      case 'offline':
        return 'Backend Offline';
      case 'checking':
        return 'Checking...';
      default:
        return 'Unknown';
    }
  };

  const formatLastChecked = () => {
    if (!lastChecked) return '';
    const now = new Date();
    const diff = Math.floor((now.getTime() - lastChecked.getTime()) / 1000);
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    return lastChecked.toLocaleTimeString();
  };

  return (
    <div className="flex items-center gap-3 rounded-lg border border-zinc-200 bg-white px-4 py-3 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-center gap-2">
        <div
          className={`h-3 w-3 rounded-full ${getStatusColor()} ${
            status === 'checking' ? 'animate-pulse' : ''
          }`}
          aria-label={status}
        />
        <span className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
          {getStatusText()}
        </span>
      </div>
      {lastChecked && status === 'online' && (
        <span className="text-xs text-zinc-500 dark:text-zinc-400">
          {formatLastChecked()}
        </span>
      )}
      {error && status === 'offline' && (
        <span className="text-xs text-red-600 dark:text-red-400" title={error}>
          {error.length > 30 ? `${error.substring(0, 30)}...` : error}
        </span>
      )}
      <button
        onClick={checkBackendStatus}
        className="ml-auto text-xs text-zinc-600 underline hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
        disabled={status === 'checking'}
        aria-label="Refresh backend status"
      >
        Refresh
      </button>
    </div>
  );
}
