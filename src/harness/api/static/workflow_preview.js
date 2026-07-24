// Read-only node-graph preview for the workflow editor.
//
// Parses the textarea's current text as workflow JSON (mirroring, not
// replacing, the server-side shape check in `_parse_workflow`), lays steps
// out in BFS-depth columns from `start`, and renders them as an SVG graph.
// This module never writes to the textarea and never issues a network
// request — it only reads `textarea.value` and rebuilds the `<svg>`'s
// children. Every string that reaches the DOM goes through `textContent`/
// `setAttribute`, never `innerHTML`, so a hostile workflow file (crafted
// `hint`/`description`/step name) can't inject markup.
(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";

  var COL_WIDTH = 168;
  var ROW_HEIGHT = 72;
  var NODE_W = 140;
  var NODE_H = 48;
  var MARGIN = 24;

  var INVALID_NOTICE = "invalid JSON — fix to preview";

  function isPlainObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  // 1. Parse + shape-validate. Anything outside the shape `_parse_workflow`
  // accepts degrades to { ok: false } rather than a partial/best-effort graph.
  function parseWorkflow(text) {
    var data;
    try {
      data = JSON.parse(text);
    } catch (e) {
      return { ok: false, reason: INVALID_NOTICE };
    }

    if (!isPlainObject(data)) {
      return { ok: false, reason: INVALID_NOTICE };
    }
    if (typeof data.start !== "string" || data.start.length === 0) {
      return { ok: false, reason: INVALID_NOTICE };
    }

    var transitions = [];
    if (data.transitions !== undefined) {
      if (!Array.isArray(data.transitions)) {
        return { ok: false, reason: INVALID_NOTICE };
      }
      for (var i = 0; i < data.transitions.length; i++) {
        var t = data.transitions[i];
        if (!isPlainObject(t)) {
          return { ok: false, reason: INVALID_NOTICE };
        }
        if (
          typeof t.from !== "string" ||
          typeof t.on !== "string" ||
          typeof t.to !== "string"
        ) {
          return { ok: false, reason: INVALID_NOTICE };
        }
        if (t.hint !== undefined && typeof t.hint !== "string") {
          return { ok: false, reason: INVALID_NOTICE };
        }
        transitions.push({
          from: t.from,
          on: t.on,
          to: t.to,
          hint: typeof t.hint === "string" ? t.hint : "",
        });
      }
    }

    return {
      ok: true,
      workflow: {
        start: data.start,
        transitions: transitions,
        maxParallel: isPlainObject(data.maxParallel) ? data.maxParallel : {},
        finishers: isPlainObject(data.finishers) ? data.finishers : {},
        descriptions: isPlainObject(data.descriptions) ? data.descriptions : {},
      },
    };
  }

  // 2. Build the node/edge graph from the validated shape. Node set mirrors
  // Workflow.steps()'s first-seen dedup rule, but (unlike steps(), which is
  // queue-oriented) deliberately includes "end" so the diagram draws it.
  function buildGraph(workflow) {
    var seen = {};
    var nodeIds = [];
    function addNode(id) {
      if (!Object.prototype.hasOwnProperty.call(seen, id)) {
        seen[id] = true;
        nodeIds.push(id);
      }
    }

    for (var i = 0; i < workflow.transitions.length; i++) {
      addNode(workflow.transitions[i].from);
      addNode(workflow.transitions[i].to);
    }
    addNode(workflow.start);

    var finishers = workflow.finishers;
    var descriptions = workflow.descriptions;

    var nodes = nodeIds.map(function (id) {
      var hasFinisher = Object.prototype.hasOwnProperty.call(finishers, id);
      var finisherKind;
      if (hasFinisher) {
        var raw = finishers[id];
        if (typeof raw === "string") {
          finisherKind = raw;
        } else if (isPlainObject(raw) && typeof raw.kind === "string") {
          finisherKind = raw.kind;
        } else {
          hasFinisher = false;
        }
      }
      return {
        id: id,
        isStart: id === workflow.start,
        isEnd: id === "end",
        hasFinisher: hasFinisher,
        finisherKind: finisherKind,
        description: typeof descriptions[id] === "string" ? descriptions[id] : undefined,
      };
    });

    var edges = workflow.transitions.map(function (t) {
      return {
        from: t.from,
        to: t.to,
        on: t.on,
        hint: t.hint,
        key: t.from + "->" + t.on + "->" + t.to,
      };
    });

    return { nodes: nodes, edges: edges };
  }

  // 3. Column/row layout. BFS depth from `start`, ties broken by first-seen
  // order; back-edges are never followed for placement (so a cycle can't
  // loop layout), a node unreachable from `start` gets a trailing column so
  // it still renders instead of vanishing.
  function computeLayout(graph) {
    var nodeIds = graph.nodes.map(function (n) {
      return n.id;
    });

    var adjacency = {};
    nodeIds.forEach(function (id) {
      adjacency[id] = [];
    });
    graph.edges.forEach(function (e) {
      if (adjacency[e.from]) {
        adjacency[e.from].push(e.to);
      }
    });

    var startNode = graph.nodes.filter(function (n) {
      return n.isStart;
    })[0];
    var startId = startNode ? startNode.id : nodeIds[0];

    var columns = {};
    if (startId !== undefined) {
      columns[startId] = 0;
      var queue = [startId];
      while (queue.length > 0) {
        var current = queue.shift();
        var neighbors = adjacency[current] || [];
        for (var i = 0; i < neighbors.length; i++) {
          var next = neighbors[i];
          if (!Object.prototype.hasOwnProperty.call(columns, next)) {
            columns[next] = columns[current] + 1;
            queue.push(next);
          }
        }
      }
    }

    var maxReachedColumn = -1;
    Object.keys(columns).forEach(function (id) {
      if (columns[id] > maxReachedColumn) {
        maxReachedColumn = columns[id];
      }
    });
    var orphanColumn = maxReachedColumn + 1;
    nodeIds.forEach(function (id) {
      if (!Object.prototype.hasOwnProperty.call(columns, id)) {
        columns[id] = orphanColumn;
      }
    });

    var rows = {};
    var columnCounts = {};
    nodeIds.forEach(function (id) {
      var col = columns[id];
      var row = columnCounts[col] || 0;
      rows[id] = row;
      columnCounts[col] = row + 1;
    });

    var columnCount = 0;
    var maxRows = 0;
    nodeIds.forEach(function (id) {
      if (columns[id] + 1 > columnCount) {
        columnCount = columns[id] + 1;
      }
    });
    Object.keys(columnCounts).forEach(function (col) {
      if (columnCounts[col] > maxRows) {
        maxRows = columnCounts[col];
      }
    });
    if (nodeIds.length > 0) {
      columnCount = Math.max(columnCount, 1);
      maxRows = Math.max(maxRows, 1);
    }

    return { columns: columns, rows: rows, columnCount: columnCount, maxRows: maxRows };
  }

  function rectFor(layout, id) {
    var col = layout.columns[id];
    var row = layout.rows[id];
    var x = MARGIN + col * COL_WIDTH;
    var y = MARGIN + row * ROW_HEIGHT;
    return {
      x: x,
      y: y,
      cx: x + NODE_W / 2,
      cy: y + NODE_H / 2,
      leftX: x,
      rightX: x + NODE_W,
      topY: y,
      bottomY: y + NODE_H,
    };
  }

  function el(tag, attrs) {
    var node = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        node.setAttribute(key, attrs[key]);
      });
    }
    return node;
  }

  function addTitle(target, text) {
    var title = document.createElementNS(SVG_NS, "title");
    title.textContent = text;
    target.appendChild(title);
  }

  function edgePath(layout, edge, siblingIndex) {
    var fromRect = rectFor(layout, edge.from);
    var toRect = rectFor(layout, edge.to);
    var isSelfLoop = edge.from === edge.to;
    var isBackEdge = !isSelfLoop && layout.columns[edge.to] <= layout.columns[edge.from];
    var bulge = 40 + 14 * siblingIndex;

    if (isSelfLoop) {
      var lx0 = fromRect.leftX + NODE_W * 0.3;
      var lx1 = fromRect.leftX + NODE_W * 0.7;
      var topY = fromRect.topY;
      var loopY = topY - bulge;
      return {
        d:
          "M " + lx0 + " " + topY +
          " C " + lx0 + " " + loopY + ", " + lx1 + " " + loopY + ", " + lx1 + " " + topY,
        labelX: (lx0 + lx1) / 2,
        labelY: loopY + 6,
      };
    }

    if (isBackEdge) {
      var bx0 = fromRect.cx;
      var by0 = fromRect.bottomY;
      var bx1 = toRect.cx;
      var by1 = toRect.bottomY;
      var bMidX = (bx0 + bx1) / 2;
      var bCtrlY = Math.max(by0, by1) + bulge;
      return {
        d: "M " + bx0 + " " + by0 + " Q " + bMidX + " " + bCtrlY + ", " + bx1 + " " + by1,
        labelX: bMidX,
        labelY: bCtrlY - 6,
      };
    }

    var x0 = fromRect.rightX;
    var y0 = fromRect.cy;
    var x1 = toRect.leftX;
    var y1 = toRect.cy;
    if (siblingIndex === 0) {
      return {
        d: "M " + x0 + " " + y0 + " L " + x1 + " " + y1,
        labelX: (x0 + x1) / 2,
        labelY: (y0 + y1) / 2 - 6,
      };
    }
    var fMidX = (x0 + x1) / 2;
    var fCtrlY = (y0 + y1) / 2 - bulge;
    return {
      d: "M " + x0 + " " + y0 + " Q " + fMidX + " " + fCtrlY + ", " + x1 + " " + y1,
      labelX: fMidX,
      labelY: fCtrlY - 6,
    };
  }

  // 4. Render. The only function touching the DOM; clears and rebuilds the
  // svg's children on every call (no incremental diffing).
  function render(svg, graph, layout) {
    while (svg.firstChild) {
      svg.removeChild(svg.firstChild);
    }

    var width = layout.columnCount * COL_WIDTH + MARGIN * 2;
    var height = layout.maxRows * ROW_HEIGHT + MARGIN * 2;
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("width", width);
    svg.setAttribute("height", height);

    var defs = el("defs");
    var marker = el("marker", {
      id: "wf-arrow",
      markerWidth: "8",
      markerHeight: "8",
      refX: "7",
      refY: "4",
      orient: "auto-start-reverse",
    });
    marker.appendChild(el("path", { d: "M0,0 L8,4 L0,8 Z", class: "wf-edge__arrowhead" }));
    defs.appendChild(marker);
    svg.appendChild(defs);

    var edgesGroup = el("g", { class: "wf-edges" });
    var siblingCounts = {};
    graph.edges.forEach(function (edge) {
      var pairKey = edge.from + "=>" + edge.to;
      var siblingIndex = siblingCounts[pairKey] || 0;
      siblingCounts[pairKey] = siblingIndex + 1;

      var path = edgePath(layout, edge, siblingIndex);
      var g = el("g", { class: "wf-edge" });

      addTitle(g, edge.hint ? edge.hint : edge.on);

      g.appendChild(el("path", { d: path.d, "marker-end": "url(#wf-arrow)" }));

      var approxWidth = Math.max(16, edge.on.length * 6.2 + 8);
      g.appendChild(
        el("rect", {
          class: "wf-edge__label-bg",
          x: path.labelX - approxWidth / 2,
          y: path.labelY - 11,
          width: approxWidth,
          height: 14,
          rx: 3,
        })
      );

      var label = el("text", {
        class: "wf-edge__label-text",
        x: path.labelX,
        y: path.labelY,
        "text-anchor": "middle",
      });
      label.textContent = edge.on;
      g.appendChild(label);

      edgesGroup.appendChild(g);
    });
    svg.appendChild(edgesGroup);

    var nodesGroup = el("g", { class: "wf-nodes" });
    graph.nodes.forEach(function (node) {
      var rect = rectFor(layout, node.id);
      var classes = ["wf-node"];
      if (node.isStart) {
        classes.push("wf-node--start");
      }
      if (node.isEnd) {
        classes.push("wf-node--end");
      }
      var g = el("g", { class: classes.join(" ") });

      if (node.description) {
        addTitle(g, node.description);
      }

      g.appendChild(el("rect", { x: rect.x, y: rect.y, width: NODE_W, height: NODE_H, rx: 8 }));

      var label = el("text", {
        class: "wf-node__label",
        x: rect.cx,
        y: rect.cy + 4,
        "text-anchor": "middle",
      });
      label.textContent = node.id;
      g.appendChild(label);

      if (node.isStart || node.isEnd) {
        var badgeText = node.isStart ? "start" : "end";
        var badgeWidth = badgeText.length * 6 + 10;
        g.appendChild(
          el("rect", {
            class: "wf-node__badge-bg" + (node.isEnd ? " wf-node__badge-bg--end" : ""),
            x: rect.x + 4,
            y: rect.y - 9,
            width: badgeWidth,
            height: 14,
            rx: 7,
          })
        );
        var badgeLabel = el("text", {
          class: "wf-node__badge-text",
          x: rect.x + 4 + badgeWidth / 2,
          y: rect.y + 1.5,
          "text-anchor": "middle",
        });
        badgeLabel.textContent = badgeText;
        g.appendChild(badgeLabel);
      }

      if (node.hasFinisher) {
        var dot = el("circle", {
          class: "wf-node__finisher-dot",
          cx: rect.x + NODE_W - 6,
          cy: rect.y + 6,
          r: 4,
        });
        addTitle(dot, "finisher: " + (node.finisherKind || ""));
        g.appendChild(dot);
      }

      nodesGroup.appendChild(g);
    });
    svg.appendChild(nodesGroup);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var textarea = document.getElementById("text");
    var svg = document.getElementById("workflow-preview-svg");
    var notice = document.getElementById("workflow-preview-notice");
    if (!textarea || !svg || !notice) {
      return;
    }

    function showNotice(reason) {
      notice.textContent = reason;
      notice.hidden = false;
      svg.hidden = true;
    }

    function hideNotice() {
      notice.hidden = true;
      svg.hidden = false;
    }

    function attempt() {
      var result = parseWorkflow(textarea.value);
      if (!result.ok) {
        showNotice(result.reason);
        return;
      }
      hideNotice();
      var graph = buildGraph(result.workflow);
      var layout = computeLayout(graph);
      render(svg, graph, layout);
    }

    attempt();

    var timer = null;
    textarea.addEventListener("input", function () {
      if (timer) {
        clearTimeout(timer);
      }
      timer = setTimeout(attempt, 150);
    });
  });
})();
