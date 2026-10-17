import { useRef, useEffect } from "react";

export function useAutoScroll(dependencies: any[]) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, dependencies);

  return containerRef;
}
