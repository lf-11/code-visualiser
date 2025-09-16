const FileInspector = (() => {
    // Most of the rendering logic from app.js is moved here.
    const LINE_HEIGHT = 21; 

    const KIND_COLORS = {
        'function': '#50fa7b',
        'class': '#ffb86c',
        'import': '#ff79c6',
        'statement_block': '#8be9fd',
        'variable_definition': '#f1fa8c',
        'comment_block': '#6272a4',
        'default': '#bd93f9',
    };

    let elements = {};

    function cacheDomElements() {
        elements = {
            editorContainer: document.getElementById('editor-container'),
            elementsOverlay: document.getElementById('elements-overlay'),
            lineNumbers: document.getElementById('line-numbers'),
            codeContent: document.getElementById('code-content'),
        };
    }

    function escapeHtml(text) {
        if (typeof text !== 'string') return '';
        return text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function updateVisibleElements() {
        if (!elements.editorContainer) return;
        const scrollTop = elements.editorContainer.scrollTop;
        const viewHeight = elements.editorContainer.clientHeight;
        const elementCards = elements.elementsOverlay.querySelectorAll('.element-card');

        elementCards.forEach(card => {
            const cardTop = card.offsetTop;
            const cardHeight = card.offsetHeight;
            
            const isVisible = (cardTop < scrollTop + viewHeight) && (cardTop + cardHeight > scrollTop);
            card.style.visibility = isVisible ? 'visible' : 'hidden';
        });
    }

    function buildElementTree(elements) {
        const elementMap = new Map(elements.map(el => [el.id, { ...el, children: [] }]));
        const roots = [];
        elementMap.forEach(node => {
            if (node.parent_id && elementMap.has(node.parent_id)) {
                elementMap.get(node.parent_id).children.push(node);
            } else {
                roots.push(node);
            }
        });
        return roots;
    }

    function calculateLineMeta(rootNodes) {
        const lineMeta = {};
        function traverse(nodes, depth) {
            nodes.forEach(node => {
                const color = KIND_COLORS[node.kind] || KIND_COLORS['default'];
                for (let i = node.start_line; i <= node.end_line; i++) {
                    if (!lineMeta[i]) lineMeta[i] = [];
                    lineMeta[i].push({ 
                        color, 
                        depth,
                        is_start: i === node.start_line,
                        is_end: i === node.end_line
                    });
                }
                if (node.children.length > 0) {
                    traverse(node.children, depth + 1);
                }
            });
        }
        traverse(rootNodes, 0);

        for (const line in lineMeta) {
            lineMeta[line].sort((a, b) => a.depth - b.depth);
        }
        return lineMeta;
    }

    function layoutTree(nodes, level, fragment) {
        const INDENT_WIDTH_PERCENT = 6;
        let lastSiblingBottomY = -1;
        const margin = 5;

        nodes.sort((a, b) => a.start_line - b.start_line);

        nodes.forEach(node => {
            const card = document.createElement('div');
            const isSingleLine = node.start_line === node.end_line;
            const color = KIND_COLORS[node.kind] || KIND_COLORS['default'];
            
            card.className = isSingleLine ? 'element-card element-card-single-line' : 'element-card';
            card.style.borderLeftColor = color;
            card.style.left = `${level * INDENT_WIDTH_PERCENT}%`;
            card.style.width = `${100 - (level * INDENT_WIDTH_PERCENT)}%`;

            if (isSingleLine) {
                card.innerHTML = `<span class="element-kind" style="background-color: ${color};">[${node.kind}]</span> <span class="element-name">${escapeHtml(node.name)}</span>`;
            } else {
                 const metadataHtml = node.metadata ? `<pre>${escapeHtml(JSON.stringify(node.metadata, null, 2))}</pre>` : '<span>None</span>';
                 const parentHtml = node.parent_id ? `<li><strong>Parent ID:</strong> ${node.parent_id}</li>` : '';
                 card.innerHTML = `
                    <div class="element-card-content">
                        <div class="element-card-header">
                            <span class="element-kind" style="background-color: ${color};">[${node.kind}]</span>
                            <span class="element-lines">L${node.start_line}-${node.end_line}</span>
                        </div>
                        <p class="element-name">${escapeHtml(node.name)}</p>
                        <ul>${parentHtml}<li><strong>Metadata:</strong> ${metadataHtml}</li></ul>
                    </div>`;
            }

            const desiredTop = (node.start_line - 1) * LINE_HEIGHT;
            const cardHeight = isSingleLine ? LINE_HEIGHT : (node.end_line - node.start_line + 1) * LINE_HEIGHT - margin;
            
            let newTop;
            if (level > 0) {
                newTop = desiredTop;
            } else {
                newTop = Math.max(desiredTop, lastSiblingBottomY + margin);
            }

            card.style.top = `${newTop}px`;
            card.style.height = `${Math.max(LINE_HEIGHT, cardHeight)}px`;

            if (level === 0) {
                 lastSiblingBottomY = newTop + cardHeight;
            }
            
            fragment.appendChild(card);

            if (node.children.length > 0) {
                layoutTree(node.children, level + 1, fragment);
            }
        });
        return fragment;
    }

    function render(data) {
        if (!elements.elementsOverlay || !elements.lineNumbers || !elements.codeContent) return;

        elements.elementsOverlay.innerHTML = '';
        elements.lineNumbers.innerHTML = '';
        elements.codeContent.innerHTML = '';

        if (!data) {
            elements.codeContent.textContent = 'Select a file to view its content.';
            return;
        }

        if (!data.elements || data.elements.length === 0) {
            elements.codeContent.textContent = data.content;
            let lineNumbersHtml = '';
            const lineCount = data.content.split('\n').length;
            for(let i=1; i <= lineCount; i++) { lineNumbersHtml += `<span>${i}</span>`; }
            elements.lineNumbers.innerHTML = lineNumbersHtml;
            return;
        }

        const rootNodes = buildElementTree(data.elements);
        const lineMeta = calculateLineMeta(rootNodes);

        const lines = data.content.split('\n');
        let lineNumbersHtml = '';
        
        const codeHtml = lines.map((line, index) => {
            const lineNumber = index + 1;
            lineNumbersHtml += `<span>${lineNumber}</span>`;

            const markersMeta = lineMeta[lineNumber] || [];
            const markersHtml = markersMeta.map(meta => 
                `<div class="element-marker" style="background-color: ${meta.color};"></div>`
            ).join('');

            const shadows = [];
            let topBorderHeight = 0;
            let bottomBorderHeight = 0;
            for (let i = markersMeta.length - 1; i >= 0; i--) {
                const meta = markersMeta[i];
                if (meta.is_start) {
                    topBorderHeight++;
                    shadows.push(`inset 0 ${topBorderHeight}px 0 0 ${meta.color}`);
                }
                if (meta.is_end) {
                    bottomBorderHeight++;
                    shadows.push(`inset 0 -${bottomBorderHeight}px 0 0 ${meta.color}`);
                }
            }
            
            const style = [];
            if (shadows.length > 0) {
                style.push(`box-shadow: ${shadows.join(', ')};`);
            }
            style.push(`padding-top: ${topBorderHeight}px;`);
            style.push(`padding-bottom: ${bottomBorderHeight}px;`);
            const lineStyle = `style="${style.join(' ')}"`;

            const escapedLine = escapeHtml(line) || ' ';
            
            return `
                <div class="code-line-container" ${lineStyle}>
                    <div class="line-markers-container">${markersHtml}</div>
                    <span class="code-text-span">${escapedLine}</span>
                </div>
            `;
        }).join('');

        elements.codeContent.innerHTML = codeHtml;
        elements.lineNumbers.innerHTML = lineNumbersHtml;
        
        const fragment = layoutTree(rootNodes, 0, document.createDocumentFragment());
        elements.elementsOverlay.appendChild(fragment);
        
        updateVisibleElements();
    }

    function init() {
        cacheDomElements();
        if (elements.editorContainer) {
            elements.editorContainer.addEventListener('scroll', updateVisibleElements);
        }
        console.log("File Inspector Initialized");
    }

    return {
        init,
        render
    };
})();


