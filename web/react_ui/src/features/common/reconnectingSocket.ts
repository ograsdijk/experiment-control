export type ReconnectBackoffOptions = {
  /** Delay before the first reconnect attempt. Defaults to 1000 ms. */
  baseMs?: number;
  /** Upper bound for the delay. Defaults to 30000 ms. */
  maxMs?: number;
  /**
   * Fraction of the delay that is randomised (0..1). Defaults to 0.25, so
   * the delay falls in [0.75, 1] x the exponential step and sockets dropped
   * together (gateway restart) do not all reconnect in the same instant.
   */
  jitterRatio?: number;
  random?: () => number;
};

/** Exponential reconnect delay with jitter; `attempt` counts from 0. */
export function reconnectDelayMs(
  attempt: number,
  {
    baseMs = 1000,
    maxMs = 30000,
    jitterRatio = 0.25,
    random = Math.random,
  }: ReconnectBackoffOptions = {}
): number {
  const step = Math.min(baseMs * 2 ** Math.max(0, attempt), maxMs);
  const ratio = Math.min(1, Math.max(0, jitterRatio));
  return Math.round(step * (1 - ratio) + step * ratio * random());
}

export type ReconnectingSocketOptions = {
  url: string;
  binaryType?: BinaryType;
  /** `reconnect` is false for the first open, true after any drop. */
  onOpen?: (info: { reconnect: boolean }) => void;
  onClose?: () => void;
  onMessage: (event: MessageEvent) => void;
  backoff?: ReconnectBackoffOptions;
};

export type ReconnectingSocket = {
  readonly isOpen: boolean;
  /** Close for good: cancels any pending reconnect. */
  close: () => void;
};

/**
 * One WebSocket that reopens itself after a drop with exponential backoff
 * and jitter. The attempt counter resets on every successful open.
 */
export function openReconnectingSocket(
  options: ReconnectingSocketOptions
): ReconnectingSocket {
  let disposed = false;
  let socket: WebSocket | null = null;
  let open = false;
  let opened = false;
  let attempt = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  const connect = () => {
    if (disposed) {
      return;
    }
    const next = new WebSocket(options.url);
    socket = next;
    if (options.binaryType) {
      next.binaryType = options.binaryType;
    }
    next.onopen = () => {
      if (disposed || socket !== next) {
        return;
      }
      attempt = 0;
      open = true;
      const reconnect = opened;
      opened = true;
      options.onOpen?.({ reconnect });
    };
    next.onclose = () => {
      if (socket !== next) {
        return;
      }
      open = false;
      socket = null;
      if (disposed) {
        return;
      }
      options.onClose?.();
      if (reconnectTimer === null) {
        const delay = reconnectDelayMs(attempt, options.backoff);
        attempt += 1;
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null;
          connect();
        }, delay);
      }
    };
    // A close event always follows an error; reconnect is handled there.
    next.onerror = () => undefined;
    next.onmessage = (event) => {
      if (!disposed && socket === next) {
        options.onMessage(event);
      }
    };
  };

  connect();
  return {
    get isOpen() {
      return open;
    },
    close: () => {
      disposed = true;
      open = false;
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      const current = socket;
      socket = null;
      current?.close();
    },
  };
}
