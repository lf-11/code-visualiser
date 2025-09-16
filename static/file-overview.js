const FileOverview = (() => {
    const KIND_COLORS = {
        'function': '#50fa7b',
        'class': '#ffb86c',
        'import': '#ff79c6',
        'statement_block': '#8be9fd',
        'variable_definition': '#f1fa8c',
        'comment_block': '#6272a4',
        'default': '#bd93f9',
    };

    const containerId = 'overview-canvas-container';
    let currentLayout = 'kind'; // 'kind' or 'position'
    let currentData = null; // Store the last fetched data

    function init() {
        const container = document.getElementById(containerId);
        if (!container) {
            console.error('Overview canvas container not found');
            return;
        }
        container.innerHTML = '<div style="color: #888; text-align: center; padding-top: 50px;">Select a file to see its overview.</div>';

        document.getElementById('view-by-kind').addEventListener('click', () => switchLayout('kind'));
        document.getElementById('view-by-position').addEventListener('click', () => switchLayout('position'));

        console.log("File Overview Initialized");
    }

    function switchLayout(newLayout) {
        if (newLayout === currentLayout) return;
        currentLayout = newLayout;
        
        document.getElementById('view-by-kind').classList.toggle('active', newLayout === 'kind');
        document.getElementById('view-by-position').classList.toggle('active', newLayout === 'position');

        render(currentData); // Re-render with the existing data
    }

    function render(data) {
        currentData = data; // Cache the data
        const container = document.getElementById(containerId);
        container.innerHTML = ''; 

        if (!data || !data.elements || data.elements.length === 0) {
            container.innerHTML = '<div style="color: #888; text-align: center; padding-top: 50px;">No code elements found in this file to display.</div>';
            return;
        }

        const { width: viewWidth, height: viewHeight } = container.getBoundingClientRect();
        const svg = d3.select(`#${containerId}`).append("svg")
            .attr("width", viewWidth)
            .attr("height", viewHeight);
        const root = svg.append("g");
        
        const zoom = d3.zoom().scaleExtent([0.1, 8]).on("zoom", (event) => root.attr("transform", event.transform));
        svg.call(zoom);

        // --- Universal Legend ---
        const uniqueKinds = [...new Set(data.elements.map(d => d.kind))];
        const legend = svg.append("g")
            .attr("class", "legend")
            .attr("transform", `translate(${viewWidth - 150}, 20)`);

        uniqueKinds.forEach((kind, i) => {
            const legendRow = legend.append("g").attr("transform", `translate(0, ${i * 20})`);
            legendRow.append("rect").attr("width", 15).attr("height", 15).attr("fill", KIND_COLORS[kind] || KIND_COLORS['default']);
            legendRow.append("text").attr("x", 20).attr("y", 12).attr("fill", "#f8f8f2").style("font-size", "12px").text(kind);
        });

        if (currentLayout === 'kind') {
            renderLayoutByKind(data, root, svg, zoom, viewWidth, viewHeight);
        } else {
            renderLayoutByPosition(data, root, svg, zoom, viewWidth, viewHeight);
        }
    }

    function renderLayoutByKind(data, root, svg, zoom, viewWidth, viewHeight) {
        const COLUMN_MARGIN = 50, RECT_VERTICAL_MARGIN = 5, TEXT_PADDING = 20;
        const MIN_RECT_HEIGHT = 35, MIN_COLUMN_WIDTH = 100, PADDING = 20, TITLE_HEIGHT = 30; // Increased MIN_RECT_HEIGHT

        const elementsByKind = data.elements.reduce((acc, el) => {
            if (!acc[el.kind]) acc[el.kind] = [];
            acc[el.kind].push({ ...el, loc: el.end_line - el.start_line + 1 });
            return acc;
        }, {});
        const kinds = Object.keys(elementsByKind).sort();

        const tempText = root.append("g").attr("opacity", 0);
        const columnWidths = {};
        kinds.forEach(kind => {
            let maxTextWidth = 0;
            elementsByKind[kind].forEach(el => {
                const textWidth = tempText.append("text").attr("class", "element-name-label").append("tspan").text(el.name).node().getComputedTextLength();
                if (textWidth > maxTextWidth) maxTextWidth = textWidth;
            });
            columnWidths[kind] = Math.max(MIN_COLUMN_WIDTH, maxTextWidth + TEXT_PADDING);
        });
        tempText.remove();

        let currentX = PADDING, maxColumnHeight = 0;
        kinds.forEach(kind => {
            let currentY = PADDING + TITLE_HEIGHT;
            const columnWidth = columnWidths[kind];
            elementsByKind[kind].sort((a, b) => b.loc - a.loc).forEach(el => {
                el.x = currentX;
                el.y = currentY;
                el.width = columnWidth;
                el.height = Math.max(MIN_RECT_HEIGHT, Math.log2(el.loc + 1) * 10); 
                currentY += el.height + RECT_VERTICAL_MARGIN;
            });
            if (currentY > maxColumnHeight) maxColumnHeight = currentY;
            currentX += columnWidth + COLUMN_MARGIN;
        });
        
        const allNodes = kinds.flatMap(kind => elementsByKind[kind]);
        const totalWidth = currentX - COLUMN_MARGIN + PADDING;
        const totalHeight = maxColumnHeight + PADDING;

        if (totalWidth > 0 && totalHeight > 0) {
            const initialScale = Math.min(viewWidth / totalWidth, viewHeight / totalHeight) * 0.9;
            const initialX = (viewWidth - totalWidth * initialScale) / 2;
            const initialY = (viewHeight - totalHeight * initialScale) / 2;
            svg.call(zoom.transform, d3.zoomIdentity.translate(initialX, initialY).scale(initialScale));
        }

        renderNodes(allNodes, root);

        let titleX = PADDING;
        kinds.forEach(kind => {
            const colWidth = columnWidths[kind];
            root.append("text").attr("x", titleX + colWidth / 2).attr("y", PADDING).attr("text-anchor", "middle").attr("fill", "#f8f8f2").style("font-size", "14px").style("font-weight", "bold").text(kind);
            titleX += colWidth + COLUMN_MARGIN;
        });
    }

    function renderLayoutByPosition(data, root, svg, zoom, viewWidth, viewHeight) {
        const RECT_VERTICAL_MARGIN = 5, TEXT_PADDING = 20;
        const MIN_RECT_HEIGHT = 35, MIN_COLUMN_WIDTH = 100, PADDING = 20; // Increased MIN_RECT_HEIGHT

        const allElements = data.elements
            .map(el => ({ ...el, loc: el.end_line - el.start_line + 1 }))
            .sort((a, b) => a.start_line - b.start_line);

        let maxTextWidth = 0;
        const tempText = root.append("g").attr("opacity", 0);
        allElements.forEach(el => {
            const textWidth = tempText.append("text").attr("class", "element-name-label").append("tspan").text(el.name).node().getComputedTextLength();
            if (textWidth > maxTextWidth) maxTextWidth = textWidth;
        });
        tempText.remove();
        
        const columnWidth = Math.max(MIN_COLUMN_WIDTH, maxTextWidth + TEXT_PADDING);
        let currentY = PADDING;
        
        allElements.forEach(el => {
            el.x = PADDING;
            el.y = currentY;
            el.width = columnWidth;
            el.height = Math.max(MIN_RECT_HEIGHT, Math.log2(el.loc + 1) * 10);
            currentY += el.height + RECT_VERTICAL_MARGIN;
        });

        const totalWidth = columnWidth + PADDING * 2;
        const totalHeight = currentY + PADDING;

        if (totalWidth > 0 && totalHeight > 0) {
            const initialScale = Math.min(viewWidth / totalWidth, viewHeight / totalHeight) * 0.9;
            const initialX = (viewWidth - totalWidth * initialScale) / 2;
            const initialY = (viewHeight - totalHeight * initialScale) / 2;
            svg.call(zoom.transform, d3.zoomIdentity.translate(initialX, initialY).scale(initialScale));
        }

        renderNodes(allElements, root);
    }

    function renderNodes(nodes, root) {
        const node = root.selectAll("g").data(nodes).join("g")
            .attr("class", "element-node")
            .attr("transform", d => `translate(${d.x}, ${d.y})`);

        node.append("rect")
            .attr("width", d => d.width)
            .attr("height", d => d.height)
            .attr("fill", d => KIND_COLORS[d.kind] || KIND_COLORS['default'])
            .attr("stroke", "#f8f8f2")
            .attr("stroke-width", 1);
            
        const text = node.append("text")
            .attr("transform", d => `translate(${d.width / 2}, ${d.height / 2 - 5})`) // Center text vertically
            .attr("class", "element-name-label");

        text.append("tspan").attr("x", 0).attr("dy", 0).text(d => d.name);
        text.append("tspan").attr("x", 0).attr("dy", "1.2em").style("font-size", "0.8em").text(d => `L${d.start_line}-${d.end_line} (${d.loc} loc)`);

        node.each(function(d) {
            const textNode = d3.select(this).select('text');
            if (textNode.node().getBBox().height > d.height - 8) { // Adjusted padding
                textNode.style("display", "none");
            }
        });
    }
    
    return {
        init,
        render
    };
})();
