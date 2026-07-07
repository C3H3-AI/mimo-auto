import { useEffect, useRef, useCallback } from "react";
import { useSessionStore } from "../store/sessionStore";

/**
 * Hook that monitors the current streaming state and provides
 * callbacks for when streaming starts/ends.
 */
export function useStreaming() {
  const stream = useSessionStore((s) => s.stream);
  const prevLoadingRef = useRef(false);

  useEffect(() => {
    prevLoadingRef.current = stream.loading;
  }, [stream.loading]);

  const isStreaming = stream.loading;

  return {
    isStreaming,
    currentText: stream.currentText,
    cancelStream: useSessionStore((s) => s.cancelStream),
  };
}

/**
 * Hook that auto-scrolls to the bottom when new messages arrive.
 */
export function useAutoScroll(
  containerRef: React.RefObject<HTMLDivElement | null>,
  deps: unknown[]
) {
  const shouldAutoScroll = useRef(true);

  const handleScroll = useCallback(() => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    // Auto-scroll if user is near bottom (within 150px)
    shouldAutoScroll.current =
      scrollHeight - scrollTop - clientHeight < 150;
  }, [containerRef]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    el.addEventListener("scroll", handleScroll);
    return () => el.removeEventListener("scroll", handleScroll);
  }, [containerRef, handleScroll]);

  useEffect(() => {
    if (shouldAutoScroll.current && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [containerRef, ...deps]);
}
