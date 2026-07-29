/**
 * 3D research landscape.
 *
 * Rendering choices that matter:
 *
 * * **One `Points` object, not one mesh per paper.** A few thousand individually
 *   picked meshes would tank the frame rate; a single buffer geometry with
 *   per-vertex colour and size scales to tens of thousands of points.
 * * **GPU picking via raycasting against the point cloud**, with a threshold
 *   scaled to point size. Cheap, and exact enough for hover at these densities.
 * * **Density surface as an optional mesh** built from the heatmap grid the
 *   backend already computed, so the "where is the literature thick" question is
 *   answered in the same view rather than a separate chart.
 * * **Seed papers (the user's own idea) rendered larger, in a reserved colour,
 *   with a ring.** The whole point of placement is to find your own work, so it
 *   must be unmistakable.
 *
 * three.js is driven directly rather than through react-three-fiber: the scene is
 * imperative and long-lived, and the reconciler would add a dependency plus a
 * render-loop indirection for no gain here.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";

import type { AnalysisDetail, AnalysisPaperRow, GapCandidate } from "../api/types";

const CLUSTER_COLOURS = [
  "#4fc1ff", "#ce9178", "#b5cea8", "#c586c0", "#dcdcaa",
  "#9cdcfe", "#f0a4a4", "#86d9ca", "#d7ba7d", "#a79df0",
];
const NOISE_COLOUR = "#5a5a5a";
const SEED_COLOUR = "#ff8c00";

export interface HoverInfo {
  row: AnalysisPaperRow;
  x: number;
  y: number;
}

interface Props {
  analysis: AnalysisDetail;
  rows: AnalysisPaperRow[];
  highlightedClusters: number[];
  selectedGapId: string;
  activeLayer: string;
  showDensity: boolean;
  showGaps: boolean;
  colourMode: "cluster" | "year" | "citations" | "density";
  onSelect: (row: AnalysisPaperRow) => void;
  onHover: (info: HoverInfo | null) => void;
}

export function Landscape3D({
  analysis,
  rows,
  highlightedClusters,
  selectedGapId,
  activeLayer,
  showDensity,
  showGaps,
  colourMode,
  onSelect,
  onHover,
}: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef<SceneState | null>(null);
  const [ready, setReady] = useState(false);

  // Callbacks are read through a ref so the scene is not torn down when a parent
  // re-render produces new function identities.
  const handlersRef = useRef({ onSelect, onHover });
  handlersRef.current = { onSelect, onHover };

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    const state = createScene(mount, handlersRef);
    stateRef.current = state;
    setReady(true);
    return () => {
      state.dispose();
      stateRef.current = null;
      setReady(false);
    };
  }, []);

  const pointData = useMemo(
    () => buildPointData(rows, colourMode, analysis),
    [rows, colourMode, analysis],
  );

  useEffect(() => {
    if (!ready || !stateRef.current) return;
    stateRef.current.setPoints(pointData, rows);
  }, [ready, pointData, rows]);

  useEffect(() => {
    if (!ready || !stateRef.current) return;
    stateRef.current.setEmphasis(highlightedClusters, rows);
  }, [ready, highlightedClusters, rows]);

  useEffect(() => {
    if (!ready || !stateRef.current) return;
    const grid = activeLayer
      ? analysis.heatmap?.layers?.[activeLayer]
      : analysis.heatmap?.grid;
    stateRef.current.setDensitySurface(
      showDensity ? grid ?? null : null,
      analysis.heatmap?.bounds ?? [],
      Boolean(activeLayer),
    );
  }, [ready, showDensity, activeLayer, analysis.heatmap]);

  useEffect(() => {
    if (!ready || !stateRef.current) return;
    stateRef.current.setGaps(showGaps ? analysis.gaps : [], selectedGapId);
  }, [ready, showGaps, analysis.gaps, selectedGapId]);

  return <div ref={mountRef} style={{ width: "100%", height: "100%" }} />;
}

// --------------------------------------------------------------- point data

interface PointData {
  positions: Float32Array;
  colours: Float32Array;
  sizes: Float32Array;
}

function buildPointData(
  rows: AnalysisPaperRow[],
  mode: Props["colourMode"],
  analysis: AnalysisDetail,
): PointData {
  const count = rows.length;
  const positions = new Float32Array(count * 3);
  const colours = new Float32Array(count * 3);
  const sizes = new Float32Array(count);

  const years = rows.map((r) => r.year ?? 0).filter((y) => y > 0);
  const minYear = years.length ? Math.min(...years) : 0;
  const maxYear = years.length ? Math.max(...years) : 0;
  const maxCitations = Math.max(1, ...rows.map((r) => r.citations));
  const colour = new THREE.Color();

  rows.forEach((row, index) => {
    positions[index * 3] = row.x;
    positions[index * 3 + 1] = row.y;
    positions[index * 3 + 2] = row.z;

    if (row.is_seed) {
      colour.set(SEED_COLOUR);
    } else if (mode === "cluster") {
      colour.set(
        row.cluster < 0 ? NOISE_COLOUR : CLUSTER_COLOURS[row.cluster % CLUSTER_COLOURS.length],
      );
    } else if (mode === "year") {
      // Old to new: deep blue through cyan to warm yellow.
      const t = maxYear > minYear && row.year ? (row.year - minYear) / (maxYear - minYear) : 0.5;
      colour.setHSL(0.62 - 0.47 * t, 0.72, 0.35 + 0.22 * t);
    } else if (mode === "citations") {
      // Log scale: raw counts span orders of magnitude.
      const t = Math.log1p(row.citations) / Math.log1p(maxCitations);
      colour.setHSL(0.58 - 0.58 * t, 0.75, 0.32 + 0.28 * t);
    } else {
      const t = Math.min(1, row.density);
      colour.setHSL(0.66 - 0.66 * t, 0.8, 0.3 + 0.3 * t);
    }
    colours[index * 3] = colour.r;
    colours[index * 3 + 1] = colour.g;
    colours[index * 3 + 2] = colour.b;

    // Highly cited work is drawn larger, so the landmarks of a field are visible
    // without reading labels. Seeds are larger still.
    const citationBoost = 1 + 0.9 * (Math.log1p(row.citations) / Math.log1p(maxCitations));
    sizes[index] = row.is_seed ? 4.6 : 1.5 * citationBoost;
  });

  void analysis;
  return { positions, colours, sizes };
}

// ------------------------------------------------------------------- scene

interface SceneState {
  setPoints(data: PointData, rows: AnalysisPaperRow[]): void;
  setEmphasis(highlighted: number[], rows: AnalysisPaperRow[]): void;
  setDensitySurface(grid: number[][] | null, bounds: number[], isLayer: boolean): void;
  setGaps(gaps: GapCandidate[], selectedId: string): void;
  dispose(): void;
}

function createScene(
  mount: HTMLDivElement,
  handlers: { current: { onSelect: Props["onSelect"]; onHover: Props["onHover"] } },
): SceneState {
  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x17181c, 46, 120);

  const camera = new THREE.PerspectiveCamera(
    52,
    mount.clientWidth / Math.max(1, mount.clientHeight),
    0.1,
    600,
  );
  camera.position.set(20, 16, 26);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setSize(mount.clientWidth, mount.clientHeight);
  // Capped at 2: beyond that the pixel cost outweighs any visible gain.
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
  renderer.setClearColor(0x17181c, 1);
  mount.appendChild(renderer.domElement);

  scene.add(new THREE.AmbientLight(0xffffff, 0.75));
  const keyLight = new THREE.DirectionalLight(0xffffff, 0.45);
  keyLight.position.set(18, 26, 14);
  scene.add(keyLight);

  // Reference grid and axes: without them a rotating point cloud loses all sense
  // of orientation.
  const grid = new THREE.GridHelper(24, 12, 0x2f3238, 0x24272c);
  grid.position.y = -11;
  scene.add(grid);
  const axes = new THREE.AxesHelper(12);
  (axes.material as THREE.Material).opacity = 0.28;
  (axes.material as THREE.Material).transparent = true;
  scene.add(axes);

  // ------------------------------------------------------------- points
  // A custom shader rather than PointsMaterial, because PointsMaterial has a
  // single global size and this map needs per-point size: highly cited work is
  // drawn larger so a field's landmarks are visible without reading labels, and
  // the user's own seed papers larger still.
  const geometry = new THREE.BufferGeometry();
  const material = new THREE.ShaderMaterial({
    uniforms: {
      uSprite: { value: circleTexture() },
      // Keeps apparent size stable across window sizes and device pixel ratios.
      uScale: { value: mount.clientHeight * renderer.getPixelRatio() * 0.5 },
    },
    vertexShader: `
      attribute float size;
      varying vec3 vColour;
      uniform float uScale;
      void main() {
        vColour = color;
        vec4 viewPosition = modelViewMatrix * vec4(position, 1.0);
        // Perspective size attenuation: divide by view-space depth.
        gl_PointSize = size * (uScale / max(1.0, -viewPosition.z)) * 0.12;
        gl_Position = projectionMatrix * viewPosition;
      }
    `,
    fragmentShader: `
      uniform sampler2D uSprite;
      varying vec3 vColour;
      void main() {
        vec4 sprite = texture2D(uSprite, gl_PointCoord);
        // Discard the transparent corners so points read as discs, not squares.
        if (sprite.a < 0.35) discard;
        gl_FragColor = vec4(vColour, sprite.a * 0.96);
      }
    `,
    vertexColors: true,
    transparent: true,
    depthWrite: true,
  });
  const points = new THREE.Points(geometry, material);
  scene.add(points);

  let currentRows: AnalysisPaperRow[] = [];
  // Pristine colours, kept so cluster emphasis can dim and restore without
  // recomputing the whole buffer.
  let baseColours: Float32Array | null = null;

  // Selection ring, moved to whichever point is hovered.
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.55, 0.75, 32),
    new THREE.MeshBasicMaterial({
      color: 0xffffff,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.9,
    }),
  );
  ring.visible = false;
  scene.add(ring);

  const gapGroup = new THREE.Group();
  scene.add(gapGroup);
  let densityMesh: THREE.Mesh | null = null;

  // -------------------------------------------------------- interaction
  const raycaster = new THREE.Raycaster();
  raycaster.params.Points = { threshold: 0.45 };
  const pointer = new THREE.Vector2();
  let hoveredIndex = -1;

  // Orbit controls, implemented inline: the full OrbitControls addon pulls in
  // more than needed and its import path differs between three.js versions.
  const spherical = new THREE.Spherical(38, Math.PI / 3.1, Math.PI / 4);
  const target = new THREE.Vector3(0, 0, 0);
  let dragging = false;
  let panning = false;
  let lastPointer = { x: 0, y: 0 };

  function applyCamera() {
    const offset = new THREE.Vector3().setFromSpherical(spherical);
    camera.position.copy(target).add(offset);
    camera.lookAt(target);
  }
  applyCamera();

  function onPointerDown(event: PointerEvent) {
    if (event.button === 0) dragging = true;
    if (event.button === 1 || event.button === 2 || event.shiftKey) panning = true;
    lastPointer = { x: event.clientX, y: event.clientY };
    (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
  }

  function onPointerUp(event: PointerEvent) {
    // A press that did not move is a click: select whatever is under it.
    const moved =
      Math.abs(event.clientX - lastPointer.x) + Math.abs(event.clientY - lastPointer.y);
    if (dragging && moved < 4 && hoveredIndex >= 0 && currentRows[hoveredIndex]) {
      handlers.current.onSelect(currentRows[hoveredIndex]);
    }
    dragging = false;
    panning = false;
  }

  function onPointerMove(event: PointerEvent) {
    const rect = renderer.domElement.getBoundingClientRect();
    if (dragging || panning) {
      const dx = event.clientX - lastPointer.x;
      const dy = event.clientY - lastPointer.y;
      lastPointer = { x: event.clientX, y: event.clientY };
      if (panning) {
        // Pan in the camera's own plane so dragging feels direct.
        const right = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 0);
        const up = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 1);
        const scale = spherical.radius * 0.0016;
        target.addScaledVector(right, -dx * scale).addScaledVector(up, dy * scale);
      } else {
        spherical.theta -= dx * 0.006;
        // Clamped short of the poles: at exactly 0 or PI the up vector degenerates
        // and the view flips.
        spherical.phi = Math.max(0.12, Math.min(Math.PI - 0.12, spherical.phi - dy * 0.006));
      }
      applyCamera();
      return;
    }

    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObject(points);
    const index = hits.length ? (hits[0].index ?? -1) : -1;
    if (index !== hoveredIndex) {
      hoveredIndex = index;
      const row = index >= 0 ? currentRows[index] : null;
      if (row) {
        ring.position.set(row.x, row.y, row.z);
        ring.visible = true;
        renderer.domElement.style.cursor = "pointer";
        handlers.current.onHover({
          row,
          x: event.clientX - rect.left,
          y: event.clientY - rect.top,
        });
      } else {
        ring.visible = false;
        renderer.domElement.style.cursor = "grab";
        handlers.current.onHover(null);
      }
    }
  }

  function onWheel(event: WheelEvent) {
    event.preventDefault();
    spherical.radius = Math.max(6, Math.min(180, spherical.radius * (1 + event.deltaY * 0.0011)));
    applyCamera();
  }

  function onContextMenu(event: Event) {
    // Right-drag pans, so the context menu would fire on every pan.
    event.preventDefault();
  }

  const element = renderer.domElement;
  element.style.cursor = "grab";
  element.addEventListener("pointerdown", onPointerDown);
  element.addEventListener("pointerup", onPointerUp);
  element.addEventListener("pointermove", onPointerMove);
  element.addEventListener("wheel", onWheel, { passive: false });
  element.addEventListener("contextmenu", onContextMenu);

  const observer = new ResizeObserver(() => {
    const width = mount.clientWidth;
    const height = Math.max(1, mount.clientHeight);
    renderer.setSize(width, height);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    // The point-size shader scales with viewport height; without this, points
    // shrink or bloat when the window is resized.
    material.uniforms.uScale.value = height * renderer.getPixelRatio() * 0.5;
  });
  observer.observe(mount);

  let frame = 0;
  let running = true;
  function animate() {
    if (!running) return;
    frame = requestAnimationFrame(animate);
    // Keep the ring facing the camera so it reads as a halo, not a disc.
    if (ring.visible) ring.quaternion.copy(camera.quaternion);
    gapGroup.children.forEach((child) => {
      if ((child as THREE.Mesh).userData.pulse) {
        const scale = 1 + 0.06 * Math.sin(Date.now() * 0.0022);
        child.scale.setScalar(scale);
      }
    });
    renderer.render(scene, camera);
  }
  animate();

  return {
    setPoints(data, rows) {
      currentRows = rows;
      baseColours = data.colours.slice();
      geometry.setAttribute("position", new THREE.BufferAttribute(data.positions, 3));
      geometry.setAttribute("color", new THREE.BufferAttribute(data.colours, 3));
      geometry.setAttribute("size", new THREE.BufferAttribute(data.sizes, 1));
      geometry.computeBoundingSphere();
      // Frame the data: a fixed camera distance is wrong for both a 20-paper and
      // a 5000-paper cloud.
      const radius = geometry.boundingSphere?.radius ?? 12;
      spherical.radius = Math.max(14, radius * 2.6);
      applyCamera();
    },

    setEmphasis(highlighted, rows) {
      if (!baseColours || !geometry.getAttribute("color")) return;
      const attribute = geometry.getAttribute("color") as THREE.BufferAttribute;
      const colours = attribute.array as Float32Array;
      const active = new Set(highlighted);
      for (let index = 0; index < rows.length; index += 1) {
        const emphasised = active.size === 0 || active.has(rows[index].cluster) || rows[index].is_seed;
        // Dim rather than hide: the shape of the whole corpus is the context that
        // makes a highlighted cluster meaningful.
        const factor = emphasised ? 1 : 0.22;
        colours[index * 3] = baseColours[index * 3] * factor;
        colours[index * 3 + 1] = baseColours[index * 3 + 1] * factor;
        colours[index * 3 + 2] = baseColours[index * 3 + 2] * factor;
      }
      attribute.needsUpdate = true;
    },

    setDensitySurface(gridValues, bounds, isLayer) {
      if (densityMesh) {
        scene.remove(densityMesh);
        densityMesh.geometry.dispose();
        (densityMesh.material as THREE.Material).dispose();
        densityMesh = null;
      }
      if (!gridValues || !gridValues.length || bounds.length < 4) return;

      const rowsCount = gridValues.length;
      const columnsCount = gridValues[0]?.length ?? 0;
      if (!columnsCount) return;
      const [xMin, xMax, yMin, yMax] = bounds;
      const surface = new THREE.PlaneGeometry(
        xMax - xMin,
        yMax - yMin,
        columnsCount - 1,
        rowsCount - 1,
      );
      const positions = surface.attributes.position as THREE.BufferAttribute;
      const colours = new Float32Array(positions.count * 3);
      const colour = new THREE.Color();
      for (let row = 0; row < rowsCount; row += 1) {
        for (let column = 0; column < columnsCount; column += 1) {
          const vertex = row * columnsCount + column;
          const value = Math.max(0, Math.min(1, gridValues[row][column] ?? 0));
          // The grid is z-up in plane space; the mesh is rotated flat below.
          positions.setZ(vertex, value * 5.2);
          if (isLayer) {
            colour.setHSL(0.09, 0.85, 0.14 + 0.5 * value);
          } else {
            colour.setHSL(0.6 - 0.5 * value, 0.7, 0.1 + 0.4 * value);
          }
          colours[vertex * 3] = colour.r;
          colours[vertex * 3 + 1] = colour.g;
          colours[vertex * 3 + 2] = colour.b;
        }
      }
      surface.setAttribute("color", new THREE.BufferAttribute(colours, 3));
      surface.computeVertexNormals();
      densityMesh = new THREE.Mesh(
        surface,
        new THREE.MeshLambertMaterial({
          vertexColors: true,
          transparent: true,
          opacity: 0.55,
          side: THREE.DoubleSide,
        }),
      );
      densityMesh.rotation.x = -Math.PI / 2;
      densityMesh.position.set((xMin + xMax) / 2, -10.6, (yMin + yMax) / 2);
      scene.add(densityMesh);
    },

    setGaps(gaps, selectedId) {
      gapGroup.clear();
      for (const gap of gaps) {
        if (gap.center.length < 3) continue;
        const selected = gap.id === selectedId;
        const radius = Math.max(0.9, Math.min(4.5, gap.radius || 1.4));
        const mesh = new THREE.Mesh(
          new THREE.SphereGeometry(radius, 20, 14),
          new THREE.MeshBasicMaterial({
            color: selected ? 0xffd479 : 0xf0a4a4,
            transparent: true,
            // Scaled by score so a weak candidate does not shout as loudly as a
            // strong one.
            opacity: selected ? 0.32 : 0.1 + 0.16 * gap.score,
            wireframe: true,
          }),
        );
        mesh.position.set(gap.center[0], gap.center[1], gap.center[2]);
        mesh.userData.pulse = selected;
        mesh.userData.gapId = gap.id;
        gapGroup.add(mesh);
      }
    },

    dispose() {
      running = false;
      cancelAnimationFrame(frame);
      observer.disconnect();
      element.removeEventListener("pointerdown", onPointerDown);
      element.removeEventListener("pointerup", onPointerUp);
      element.removeEventListener("pointermove", onPointerMove);
      element.removeEventListener("wheel", onWheel);
      element.removeEventListener("contextmenu", onContextMenu);
      geometry.dispose();
      (material.uniforms.uSprite.value as THREE.Texture)?.dispose();
      material.dispose();
      gapGroup.clear();
      if (densityMesh) {
        densityMesh.geometry.dispose();
        (densityMesh.material as THREE.Material).dispose();
      }
      ring.geometry.dispose();
      (ring.material as THREE.Material).dispose();
      renderer.dispose();
      if (element.parentElement === mount) mount.removeChild(element);
    },
  };
}

/** Round point sprite. Generated once and shared by every point. */
function circleTexture(): THREE.Texture {
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const context = canvas.getContext("2d")!;
  const gradient = context.createRadialGradient(
    size / 2, size / 2, 0, size / 2, size / 2, size / 2,
  );
  gradient.addColorStop(0, "rgba(255,255,255,1)");
  gradient.addColorStop(0.62, "rgba(255,255,255,0.95)");
  gradient.addColorStop(1, "rgba(255,255,255,0)");
  context.fillStyle = gradient;
  context.beginPath();
  context.arc(size / 2, size / 2, size / 2, 0, Math.PI * 2);
  context.fill();
  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

export { CLUSTER_COLOURS, NOISE_COLOUR, SEED_COLOUR };
