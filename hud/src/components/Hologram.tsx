import { useEffect, useRef } from "react";
import {
  AmbientLight,
  Group,
  IcosahedronGeometry,
  Mesh,
  MeshBasicMaterial,
  PerspectiveCamera,
  Scene,
  TorusGeometry,
  WebGLRenderer,
} from "three";
import { dragRotation, RING_COLORS, spinSpeed, type Rotation } from "../hologram";
import type { Ring } from "../state";

interface Props {
  ring: Ring;
  /** Ohne WebGL (alte Grafik, Fernzugriff): zurück zur flachen Ansicht. */
  onFail: () => void;
}

/** Drehbare 3D-Ansicht des Rings als Hologramm-Ersatz (M3.10). Mit der Maus ziehen dreht sie. */
export default function Hologram({ ring, onFail }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const ringRef = useRef(ring);
  useEffect(() => {
    ringRef.current = ring;
  }, [ring]);

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    let renderer: WebGLRenderer;
    try {
      renderer = new WebGLRenderer({ alpha: true, antialias: true });
    } catch {
      onFail();
      return;
    }
    renderer.setPixelRatio(Math.min(globalThis.devicePixelRatio || 1, 2));
    const fit = () => {
      const size = element.clientWidth || 320;
      renderer.setSize(size, size, false); // Zeichenpuffer zur Größe, CSS bleibt zuständig
    };
    fit();
    element.appendChild(renderer.domElement);
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(fit);
    resize?.observe(element);
    // GPU-Reset oder Ruhezustand: zurück zum flachen Ring statt eines schwarzen Bildes.
    const lost = (event: Event) => {
      event.preventDefault();
      onFail();
    };
    renderer.domElement.addEventListener("webglcontextlost", lost);

    const scene = new Scene();
    const camera = new PerspectiveCamera(40, 1, 0.1, 100);
    camera.position.set(0, 0, 7);
    scene.add(new AmbientLight(0xffffff, 1));
    const group = new Group();
    const material = new MeshBasicMaterial({ color: RING_COLORS[ringRef.current], wireframe: true, transparent: true, opacity: 0.8 });
    const geometries = [
      new TorusGeometry(2.2, 0.05, 8, 96),
      new TorusGeometry(1.7, 0.18, 6, 48),
      new TorusGeometry(1.25, 0.04, 8, 72),
      new IcosahedronGeometry(0.7, 1),
    ];
    const meshes = geometries.map((geometry) => new Mesh(geometry, material));
    meshes[1]?.rotation.set(Math.PI / 2.4, 0, 0);
    meshes[2]?.rotation.set(0, Math.PI / 3, 0);
    meshes.forEach((mesh) => group.add(mesh));
    scene.add(group);

    const still = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    let rotation: Rotation = { x: 0.35, y: 0 };
    let dragging: { x: number; y: number } | null = null;
    const down = (event: PointerEvent) => {
      dragging = { x: event.clientX, y: event.clientY };
      renderer.domElement.setPointerCapture?.(event.pointerId);
    };
    const move = (event: PointerEvent) => {
      if (!dragging) return;
      rotation = dragRotation(rotation, event.clientX - dragging.x, event.clientY - dragging.y);
      dragging = { x: event.clientX, y: event.clientY };
    };
    const up = () => (dragging = null);
    renderer.domElement.addEventListener("pointerdown", down);
    renderer.domElement.addEventListener("pointermove", move);
    renderer.domElement.addEventListener("pointerup", up);
    renderer.domElement.addEventListener("pointercancel", up);

    // Höchstens 30 Bilder je Sekunde, bei reduzierter Bewegung 4, im Hintergrund keine: das HUD
    // läuft dauernd und soll die Grafik nicht belasten.
    const interval = still ? 250 : 33;
    let frame = 0;
    let last = performance.now();
    const draw = (time: number) => {
      frame = requestAnimationFrame(draw);
      if (time - last < interval || document.hidden) return;
      const seconds = Math.min(0.1, (time - last) / 1000);
      last = time;
      const current = ringRef.current;
      material.color.setHex(RING_COLORS[current]);
      if (!dragging && !still) rotation = { ...rotation, y: rotation.y + seconds * spinSpeed(current) * Math.PI * 2 * 0.25 };
      group.rotation.set(rotation.x, rotation.y, 0);
      if (!still) meshes[3]?.rotation.set(time / 2400, time / 1800, 0);
      renderer.render(scene, camera);
    };
    frame = requestAnimationFrame(draw);

    return () => {
      cancelAnimationFrame(frame);
      resize?.disconnect();
      renderer.domElement.removeEventListener("webglcontextlost", lost);
      renderer.domElement.removeEventListener("pointerdown", down);
      renderer.domElement.removeEventListener("pointermove", move);
      renderer.domElement.removeEventListener("pointerup", up);
      renderer.domElement.removeEventListener("pointercancel", up);
      geometries.forEach((geometry) => geometry.dispose());
      material.dispose();
      renderer.forceContextLoss(); // dispose() allein gibt den WebGL-Kontext nicht frei
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [onFail]);

  return <div ref={host} className="hologram" title="Mit der Maus ziehen zum Drehen" />;
}
