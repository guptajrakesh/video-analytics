import { useEffect, useState } from "react";

// SVG chart libraries need concrete colours, not CSS variables, so read the
// design tokens from the page and re-read when the OS theme flips.
function read() {
  const style = getComputedStyle(document.documentElement);
  const token = (name) => style.getPropertyValue(name).trim();
  return {
    series1: token("--series-1"),
    series2: token("--series-2"),
    gridline: token("--gridline"),
    muted: token("--text-muted"),
    surface: token("--surface-1"),
    ink: token("--text-primary"),
  };
}

export function useChartColors() {
  const [colors, setColors] = useState(read);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setColors(read());
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return colors;
}
