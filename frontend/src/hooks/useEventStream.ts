import { useEffect, useRef } from 'react';

const API_URL = import.meta.env.VITE_API_URL || '';

const RECONNECT_BASE_MS = 3000;
const RECONNECT_MAX_MS = 30000;

function toWsUrl(httpUrl: string, topic: string): string {
  const url = new URL(httpUrl || '/', window.location.origin);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.pathname = '/ws';
  url.search = new URLSearchParams({ topic }).toString();
  return url.toString();
}

export function useEventStream(topic: string, onMessage: (message: any) => void) {
  // Keep the handler in a ref so the socket effect only re-runs when `topic`
  // changes. Without this, every parent re-render that produces a new
  // onMessage identity (common when callback deps update) tears down the
  // WebSocket mid-handshake — the browser logs that as "closed before
  // connection established".
  const onMessageRef = useRef(onMessage);
  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    let closed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    let retryDelayMs = RECONNECT_BASE_MS;

    const connect = () => {
      socket = new WebSocket(toWsUrl(API_URL, topic));
      socket.onopen = () => {
        retryDelayMs = RECONNECT_BASE_MS;
      };
      socket.onmessage = (event) => {
        // Drop messages from a socket that's being torn down. When `topic`
        // changes, cleanup sets closed=true and calls socket.close(), but
        // close is async — a queued message can still fire here against
        // the new topic's onMessage handler. Late messages from the old
        // topic would deliver the wrong payload to the new subscriber.
        if (closed) return;
        try {
          onMessageRef.current(JSON.parse(event.data));
        } catch {
          onMessageRef.current({ type: 'message', payload: event.data });
        }
      };
      socket.onclose = (event) => {
        if (closed) return;
        // 1008 = the backend rejected the session before accept()
        // (backend/routers/ws.py). Redialing would loop forever against the
        // same rejection — stop and let the auth layer react instead.
        if (event.code === 1008) {
          window.dispatchEvent(new CustomEvent('sentinel:ws-unauthorized', { detail: { topic } }));
          return;
        }
        // Capped exponential backoff so a down backend isn't hammered at a
        // fixed 3s per topic; reset to base on a successful open.
        reconnectTimer = window.setTimeout(connect, retryDelayMs);
        retryDelayMs = Math.min(retryDelayMs * 2, RECONNECT_MAX_MS);
      };
      socket.onerror = () => socket?.close();
    };

    connect();

    return () => {
      closed = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [topic]);
}
