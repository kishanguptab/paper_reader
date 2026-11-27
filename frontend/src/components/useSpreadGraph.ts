import { useEffect, useRef } from "react";
import { forceCollide, forceX, forceY } from "d3-force";

/** Readable layout for labelled nodes: repulsion, collision spacing (so labels
 * don't stack), gentle centering (so unconnected nodes don't drift off), then fit. */
export function useSpreadGraph(deps: unknown[], charge = -260, distance = 80, collide = 30) {
  const ref = useRef<any>(null);
  useEffect(() => {
    const t = setTimeout(() => {
      const fg = ref.current;
      if (!fg) return;
      fg.d3Force("charge")?.strength(charge).distanceMax(400);
      fg.d3Force("link")?.distance(distance);
      fg.d3Force("collide", forceCollide(collide));
      fg.d3Force("x", forceX(0).strength(0.06));
      fg.d3Force("y", forceY(0).strength(0.06));
      fg.d3ReheatSimulation?.();
    }, 0);
    return () => clearTimeout(t);
  }, deps);
  const onEngineStop = () => ref.current?.zoomToFit(500, 40);
  return { ref, onEngineStop };
}
