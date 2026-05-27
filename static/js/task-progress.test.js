import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Mock fetch globally
const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

// Mock EventSource
class MockEventSource {
  constructor(url) {
    this.url = url;
    this.onmessage = null;
    this.onerror = null;
    this.readyState = 1; // OPEN
    MockEventSource.instances.push(this);
  }
  addEventListener(event, handler) {
    this[`_on${event}`] = handler;
  }
  close() {
    this.readyState = 2; // CLOSED
  }
  static instances = [];
  static reset() {
    MockEventSource.instances = [];
  }
}
vi.stubGlobal('EventSource', MockEventSource);

// Load and eval the source file (it defines a global class, no exports)
const src = readFileSync(join(__dirname, 'task-progress.js'), 'utf-8');
// Wrap in a function that assigns to globalThis
const wrappedSrc = src + '\nglobalThis.TaskProgress = TaskProgress;';
const fn = new Function(wrappedSrc);
fn();

function createDom() {
  document.body.innerHTML = `
    <div id="container" style="display:none">
      <div id="bar" style="width:0%"></div>
      <span id="status"></span>
      <span id="percent"></span>
      <span id="detail"></span>
    </div>
  `;
}

function createTaskProgress(overrides = {}) {
  return new TaskProgress({
    taskId: 'test_task',
    container: '#container',
    bar: '#bar',
    status: '#status',
    percent: '#percent',
    detail: '#detail',
    onComplete: vi.fn(),
    onError: vi.fn(),
    onProgress: vi.fn(),
    ...overrides,
  });
}

describe('TaskProgress', () => {
  beforeEach(() => {
    createDom();
    MockEventSource.reset();
    vi.useFakeTimers();
    mockFetch.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('constructor', () => {
    it('initializes with default state', () => {
      const tp = createTaskProgress();
      expect(tp._done).toBe(false);
      expect(tp._seenRunning).toBe(false);
      expect(tp._eventSource).toBeNull();
      expect(tp._pollTimer).toBeNull();
    });
  });

  describe('_updateUI', () => {
    it('sets bar width, percent text, and status text', () => {
      const tp = createTaskProgress();
      tp._updateUI({ percent: 50, msg: 'Processing...' });

      expect(document.getElementById('bar').style.width).toBe('50%');
      expect(document.getElementById('percent').textContent).toBe('50%');
      expect(document.getElementById('status').textContent).toBe('Processing...');
    });

    it('uses progress field as fallback for percent', () => {
      const tp = createTaskProgress();
      tp._updateUI({ progress: 75, message: 'Working' });

      expect(document.getElementById('bar').style.width).toBe('75%');
      expect(document.getElementById('percent').textContent).toBe('75%');
      expect(document.getElementById('status').textContent).toBe('Working');
    });

    it('calls onProgress callback', () => {
      const onProgress = vi.fn();
      const tp = createTaskProgress({ onProgress });
      tp._updateUI({ percent: 30, msg: 'test' });
      expect(onProgress).toHaveBeenCalledWith({ percent: 30, msg: 'test' });
    });

    it('updates detail element when detail field present', () => {
      const tp = createTaskProgress();
      tp._updateUI({ percent: 50, msg: 'test', detail: '42 messages' });
      expect(document.getElementById('detail').textContent).toBe('42 messages');
    });
  });

  describe('_handleDone', () => {
    it('calls onComplete on success', () => {
      const onComplete = vi.fn();
      const tp = createTaskProgress({ onComplete });
      tp._handleDone({ percent: 100, msg: 'Done!' });

      expect(tp._done).toBe(true);
      expect(onComplete).toHaveBeenCalled();
      expect(document.getElementById('percent').textContent).toBe('100%');
    });

    it('calls onError on error', () => {
      const onError = vi.fn();
      const tp = createTaskProgress({ onError });
      tp._handleDone({ error: true, msg: 'Failed' });

      expect(tp._done).toBe(true);
      expect(onError).toHaveBeenCalledWith('Failed');
    });

    it('calls stop()', () => {
      const tp = createTaskProgress();
      const stopSpy = vi.spyOn(tp, 'stop');
      tp._handleDone({ msg: 'Done' });
      expect(stopSpy).toHaveBeenCalled();
    });
  });

  describe('startPolling', () => {
    it('does not fire _handleDone on stale completed task', async () => {
      const onComplete = vi.fn();
      const onError = vi.fn();
      const tp = createTaskProgress({ onComplete, onError });

      // First poll returns a completed (stale) task
      mockFetch.mockResolvedValueOnce({
        json: () => Promise.resolve({ done: true, percent: 100, msg: 'Old result' }),
      });

      tp.startPolling(1000);

      // Wait for the first poll
      await vi.advanceTimersByTimeAsync(100);

      // Should NOT have fired done because _seenRunning is false
      expect(onComplete).not.toHaveBeenCalled();
      expect(tp._done).toBe(false);
    });

    it('fires _handleDone after seeing running then done', async () => {
      const onComplete = vi.fn();
      const tp = createTaskProgress({ onComplete });

      // First poll: running
      mockFetch.mockResolvedValueOnce({
        json: () => Promise.resolve({ done: false, percent: 30, msg: 'Working' }),
      });
      // Second poll: done
      mockFetch.mockResolvedValueOnce({
        json: () => Promise.resolve({ done: true, percent: 100, msg: 'Complete' }),
      });

      tp.startPolling(1000);

      // First poll
      await vi.advanceTimersByTimeAsync(100);
      expect(tp._seenRunning).toBe(true);
      expect(onComplete).not.toHaveBeenCalled();

      // Second poll
      await vi.advanceTimersByTimeAsync(1000);
      expect(onComplete).toHaveBeenCalled();
      expect(tp._done).toBe(true);
    });

    it('updates UI on running task', async () => {
      const onProgress = vi.fn();
      const tp = createTaskProgress({ onProgress });

      mockFetch.mockResolvedValueOnce({
        json: () => Promise.resolve({ done: false, percent: 42, msg: 'Syncing...' }),
      });

      tp.startPolling(1000);
      await vi.advanceTimersByTimeAsync(100);

      expect(document.getElementById('percent').textContent).toBe('42%');
      expect(document.getElementById('status').textContent).toBe('Syncing...');
      expect(onProgress).toHaveBeenCalled();
    });

    it('shows container on start', () => {
      const tp = createTaskProgress();
      document.getElementById('container').style.display = 'none';
      mockFetch.mockResolvedValue({ json: () => Promise.resolve({ done: true }) });
      tp.startPolling(1000);
      expect(document.getElementById('container').style.display).toBe('block');
    });

    it('calls onError on fetch failure', async () => {
      const onError = vi.fn();
      const tp = createTaskProgress({ onError });

      mockFetch.mockRejectedValueOnce(new Error('Network error'));

      tp.startPolling(1000);
      await vi.advanceTimersByTimeAsync(100);

      expect(onError).toHaveBeenCalledWith('无法获取任务状态');
    });
  });

  describe('restore', () => {
    it('returns true and starts polling when task is running', async () => {
      mockFetch.mockResolvedValueOnce({
        json: () => Promise.resolve({ done: false, percent: 60, msg: 'Running' }),
      });

      const tp = createTaskProgress();
      const startPollingSpy = vi.spyOn(tp, 'startPolling');
      const result = await tp.restore();

      expect(result).toBe(true);
      expect(startPollingSpy).toHaveBeenCalled();
      expect(document.getElementById('container').style.display).toBe('block');
    });

    it('returns false when task is done', async () => {
      mockFetch.mockResolvedValueOnce({
        json: () => Promise.resolve({ done: true, percent: 100 }),
      });

      const tp = createTaskProgress();
      const result = await tp.restore();

      expect(result).toBe(false);
      expect(tp._pollTimer).toBeNull();
    });

    it('returns false when task not found', async () => {
      mockFetch.mockResolvedValueOnce({
        json: () => Promise.resolve(null),
      });

      const tp = createTaskProgress();
      const result = await tp.restore();

      expect(result).toBe(false);
    });

    it('returns false on fetch error', async () => {
      mockFetch.mockRejectedValueOnce(new Error('fail'));

      const tp = createTaskProgress();
      const result = await tp.restore();

      expect(result).toBe(false);
    });
  });

  describe('stop', () => {
    it('closes EventSource', () => {
      const tp = createTaskProgress();
      const es = new MockEventSource('/test');
      tp._eventSource = es;
      tp.stop();
      expect(es.readyState).toBe(2); // CLOSED
      expect(tp._eventSource).toBeNull();
    });

    it('clears poll timer', () => {
      const tp = createTaskProgress();
      tp._pollTimer = setInterval(() => {}, 1000);
      tp.stop();
      expect(tp._pollTimer).toBeNull();
    });
  });

  describe('cancel', () => {
    it('calls cancel API endpoint', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true });

      const tp = createTaskProgress();
      await tp.cancel();

      expect(mockFetch).toHaveBeenCalledWith(
        '/api/tasks/test_task/cancel',
        { method: 'POST' }
      );
    });

    it('falls back to legacy endpoint on error', async () => {
      mockFetch.mockRejectedValueOnce(new Error('fail'));
      mockFetch.mockResolvedValueOnce({ ok: true });

      const tp = createTaskProgress();
      await tp.cancel();

      expect(mockFetch).toHaveBeenCalledWith(
        '/api/sync/stop',
        { method: 'POST' }
      );
    });

    it('stops and hides after cancel', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true });

      const tp = createTaskProgress();
      const stopSpy = vi.spyOn(tp, 'stop');
      await tp.cancel();

      expect(stopSpy).toHaveBeenCalled();
      expect(document.getElementById('container').style.display).toBe('none');
    });
  });

  describe('_show / _hide', () => {
    it('show sets display to block', () => {
      const tp = createTaskProgress();
      tp._show();
      expect(document.getElementById('container').style.display).toBe('block');
    });

    it('hide sets display to none', () => {
      const tp = createTaskProgress();
      document.getElementById('container').style.display = 'block';
      tp._hide();
      expect(document.getElementById('container').style.display).toBe('none');
    });

    it('handles missing container gracefully', () => {
      const tp = createTaskProgress({ container: '#nonexistent' });
      expect(() => tp._show()).not.toThrow();
      expect(() => tp._hide()).not.toThrow();
    });
  });
});
