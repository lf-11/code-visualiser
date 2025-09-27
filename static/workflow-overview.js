const WorkflowOverview = (() => {
    let container = null;
    let controlsContainer = null;
    let workflowSelect = null;
    let workflowsData = null;

    function init() {
        container = document.getElementById('workflow-canvas-container');
        controlsContainer = document.getElementById('workflow-controls');
        workflowSelect = document.getElementById('workflow-select');
        
        if (!container || !controlsContainer || !workflowSelect) {
            console.error('Workflow overview elements not found');
            return;
        }

        fetchDataAndRender();
        workflowSelect.addEventListener('change', handleSelectionChange);
    }

    async function fetchDataAndRender() {
        try {
            const response = await fetch('/workflow_trace.json');
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            
            workflowsData = await response.json();
            
            populateDropdown(workflowsData);
            
            if (workflowsData && workflowsData.length > 0) {
                render(workflowsData[0]);
            }
        } catch (error) {
            console.error("Failed to fetch workflow trace:", error);
            container.innerHTML = `<p class="error">Failed to load workflow data.</p>`;
        }
    }

    function populateDropdown(workflows) {
        workflowSelect.innerHTML = '';
        workflows.forEach((flow, index) => {
            const option = document.createElement('option');
            option.value = index;
            option.textContent = flow.workflow_name;
            workflowSelect.appendChild(option);
        });
    }

    function handleSelectionChange() {
        const selectedIndex = workflowSelect.value;
        if (workflowsData && workflowsData[selectedIndex]) {
            render(workflowsData[selectedIndex]);
        }
    }

    function transformPythonTrace(node) {
        if (!node) return null;
        const children = (node.callees || []).map(transformPythonTrace);
        return { ...node, children };
    }

    function transformJavascriptTrace(nodes) {
        if (!nodes || nodes.length === 0) return [];
        
        function transformNode(node) {
            if (!node) return null;
            const children = (node.callers || []).map(transformNode);
            return { ...node, children };
        }
        
        return nodes.map(transformNode);
    }

    function render(workflow) {
        if (!workflow) {
            container.innerHTML = '<p>No workflow data to display.</p>';
            return;
        }

        container.innerHTML = ''; // Clear previous render

        const width = container.clientWidth;
        const height = container.clientHeight;

        if (width === 0 || height === 0) {
            // Container is not visible, do not render.
            // It will be rendered correctly when the tab is clicked.
            return;
        }

        const nodeHeight = 40; // Reduced for vertical compactness
        const nodeWidth = 180;

        const svg = d3.select(container).append("svg")
            .attr("width", width)
            .attr("height", height);

        const g = svg.append("g");
        
        // --- Prepare data ---
        const endpointNode = { name: workflow.endpoint.name, path: workflow.endpoint.path, kind: 'endpoint' };
        
        const pyHierarchy = workflow.python_trace ? d3.hierarchy(transformPythonTrace(workflow.python_trace)) : null;
        const jsHierarchies = transformJavascriptTrace(workflow.javascript_trace).map(jsRoot => d3.hierarchy(jsRoot));

        let pyNodes = [], pyLinks = [], jsNodes = [], jsLinks = [];

        // --- Python layout (left side) ---
        if (pyHierarchy) {
            const pyTree = d3.tree().nodeSize([nodeHeight, nodeWidth]);
            pyTree(pyHierarchy);
            pyNodes = pyHierarchy.descendants();
            pyLinks = pyHierarchy.links();
            pyNodes.forEach(node => {
                node.y = -(node.depth + 1) * nodeWidth;
            });
            // Center the tree vertically
            const pyXCoords = pyNodes.map(d => d.x);
            const pyMinX = d3.min(pyXCoords) || 0;
            const pyTreeHeight = (d3.max(pyXCoords) || 0) - pyMinX;
            const pyXOffset = -pyMinX - pyTreeHeight / 2;
            pyNodes.forEach(node => node.x += pyXOffset);
        }

        // --- JavaScript layout (right side) ---
        if (jsHierarchies.length > 0) {
            const jsTree = d3.tree().nodeSize([nodeHeight, nodeWidth]);
            
            const laidOutHierarchies = jsHierarchies.map(h => {
                jsTree(h);
                const descendants = h.descendants();
                const xCoords = descendants.map(d => d.x);
                const minX = d3.min(xCoords);
                const height = d3.max(xCoords) - minX;
                return { hierarchy: h, minX, height, descendants, links: h.links() };
            });

            const totalJsHeight = d3.sum(laidOutHierarchies, d => d.height) + (jsHierarchies.length - 1) * nodeHeight;
            let currentXOffset = -totalJsHeight / 2;

            laidOutHierarchies.forEach(({ hierarchy, minX, height, descendants, links }) => {
                descendants.forEach(node => {
                    node.y = (node.depth + 1) * nodeWidth;
                    node.x += currentXOffset - minX;
                });
                jsNodes.push(...descendants);
                jsLinks.push(...links);
                currentXOffset += height + nodeHeight;
            });
        }

        // Create links from endpoint to roots
        if (pyHierarchy) pyLinks.push({ source: { x: 0, y: 0 }, target: pyHierarchy });
        jsHierarchies.forEach(h => jsLinks.push({ source: { x: 0, y: 0 }, target: h }));
        
        const endpointHierarchyNode = { data: endpointNode, x: 0, y: 0, depth: 0 };
        const allNodes = [ ...pyNodes, ...jsNodes, endpointHierarchyNode ];
        const allLinks = [...pyLinks, ...jsLinks];

        // --- Render links ---
        const linkGenerator = d3.linkHorizontal().x(d => d.y).y(d => d.x);
        
        g.selectAll('.link').data(allLinks).join('path')
            .attr('class', 'link').attr('d', linkGenerator);

        // --- Render nodes ---
        const node = g.selectAll("g.node")
            .data(allNodes)
            .join("g")
            .attr("class", "node")
            .attr("transform", d => `translate(${d.y},${d.x})`);

        const getNodeColor = (d) => {
            if (d.data.kind === 'endpoint') return 'var(--accent-ok)';
            return d.y < 0 ? 'var(--accent-warn)' : 'var(--accent-color)'; // Python vs JS
        };

        node.append("circle")
            .attr("r", 8)
            .style("fill", getNodeColor);

        const text = node.append("text")
            .attr("x", d => d.y < 0 ? -15 : 15)
            .attr("dy", "0.31em")
            .attr("text-anchor", d => d.y < 0 ? "end" : "start");
        
        text.append('tspan').attr('class', 'name').text(d => d.data.name);
        text.append('tspan').attr('class', 'path')
            .attr('x', d => d.y < 0 ? -15 : 15)
            .attr('dy', '1.2em')
            .text(d => d.data.path);

        // --- Zoom and Pan ---
        const xPadding = 40;
        const yPadding = 120;
        const minX = d3.min(allNodes, d => d.x) - xPadding;
        const maxX = d3.max(allNodes, d => d.x) + xPadding;
        const minY = d3.min(allNodes, d => d.y) - yPadding;
        const maxY = d3.max(allNodes, d => d.y) + yPadding;

        const graphWidth = maxY - minY;
        const graphHeight = maxX - minX;

        const zoom = d3.zoom()
            .extent([[0, 0], [width, height]])
            .scaleExtent([0.1, 5])
            .on("zoom", ({transform}) => g.attr("transform", transform));
        
        svg.call(zoom);

        if (graphWidth > 0 && graphHeight > 0) {
            const scale = Math.min(width / graphWidth, height / graphHeight) * 0.9;
            const translateX = (width - graphWidth * scale) / 2 - minY * scale;
            const translateY = (height - graphHeight * scale) / 2 - minX * scale;
            svg.call(zoom.transform, d3.zoomIdentity.translate(translateX, translateY).scale(scale));
        }
    }

    return {
        init,
        render: (workflow) => { 
            // The render is now mostly internal, but we can expose it if needed
            if (workflow) render(workflow); 
        }
    };
})();
