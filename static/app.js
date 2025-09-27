document.addEventListener('DOMContentLoaded', () => {
    // --- STATE ---
    let projectsData = [];
    let currentFileDetails = null;
    let unparsedFileIds = new Set();

    // --- DOM ELEMENTS ---
    const elements = {
        projectSelect: document.getElementById('project-select'),
        fileSelect: document.getElementById('file-select'),
        prevFileBtn: document.getElementById('prev-file-btn'),
        nextFileBtn: document.getElementById('next-file-btn'),
        parseBtn: document.getElementById('parse-project-btn'),
        statusMessage: document.getElementById('status-message'),
        projectContextControls: document.getElementById('project-context-controls'),
        issueControls: document.getElementById('issue-controls'),
        jumpToIssueBtn: document.getElementById('jump-to-issue-btn'),
        fileIssueIndicator: document.getElementById('file-issue-indicator'),
        projectParsingStatus: document.getElementById('project-parsing-status'),
        projectParsingSummary: document.getElementById('project-parsing-summary'),
        unparsedFilesPopup: document.getElementById('unparsed-files-popup'),
        unparsedFilesCloseBtn: document.getElementById('unparsed-files-close-btn'),
        unparsedFilesList: document.getElementById('unparsed-files-list'),
        tabs: document.querySelectorAll('.tab-link'),
        tabContents: document.querySelectorAll('.tab-content')
    };

    // --- CORE FUNCTIONS ---

    function setStatus(message, type = 'info') {
        elements.statusMessage.textContent = message;
        elements.statusMessage.className = type;
    }

    async function fetchProjects() {
        try {
            setStatus('Loading projects...', 'info');
            const response = await fetch('/api/projects');
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            
            projectsData = await response.json();

            const selectedValue = elements.projectSelect.value;
            elements.projectSelect.innerHTML = '<option value="">-- Select a Project --</option>';

            projectsData.forEach(project => {
                const option = document.createElement('option');
                option.value = project.name;
                option.textContent = `${project.name} ${project.is_parsed ? '(Parsed)' : '(Not Parsed)'}`;
                option.dataset.isParsed = project.is_parsed;
                option.dataset.path = project.path;
                elements.projectSelect.appendChild(option);
            });
            
            if (selectedValue) {
                elements.projectSelect.value = selectedValue;
            }
            
            setStatus('Projects loaded.', 'success');
            handleProjectSelection();

        } catch (error) {
            console.error("Failed to fetch projects:", error);
            elements.projectSelect.innerHTML = '<option>Error loading projects</option>';
            setStatus('Error loading projects.', 'error');
        }
    }

    async function handleProjectSelection() {
        const selectedOption = elements.projectSelect.options[elements.projectSelect.selectedIndex];
        
        elements.fileSelect.innerHTML = '<option>Select a project first</option>';
        elements.fileSelect.disabled = true;
        elements.projectParsingStatus.classList.add('hidden');
        elements.unparsedFilesPopup.classList.add('hidden');
        elements.issueControls.classList.add('hidden');
        
        // Reset views
        currentFileDetails = null;
        updateAllViews();

        if (!selectedOption || !selectedOption.value) {
            elements.projectContextControls.classList.add('hidden');
            return;
        }

        elements.projectContextControls.classList.remove('hidden');
        const projectName = selectedOption.value;
        const isParsed = selectedOption.dataset.isParsed === 'true';

        if (isParsed) {
            elements.parseBtn.textContent = 'Re-parse Project';
            await fetchFilesForProject(projectName);
            await checkProjectParsingStatus(projectName);
        } else {
            elements.parseBtn.textContent = 'Parse Project';
        }
    }

    async function checkProjectParsingStatus(projectName) {
        try {
            const response = await fetch(`/api/projects/${projectName}/parsing_status`);
            if (!response.ok) throw new Error('Failed to fetch parsing status');
            const unparsedFiles = await response.json();

            elements.unparsedFilesList.innerHTML = '';
            unparsedFileIds.clear();

            if (unparsedFiles.length === 0) {
                elements.projectParsingStatus.classList.add('hidden');
                elements.projectParsingSummary.textContent = '';
                elements.unparsedFilesPopup.classList.add('hidden');
                elements.issueControls.classList.add('hidden');
            } else {
                elements.projectParsingStatus.classList.remove('hidden');
                elements.issueControls.classList.remove('hidden');
                elements.projectParsingStatus.className = 'warning';
                elements.projectParsingSummary.textContent = `${unparsedFiles.length} file(s) need attention`;

                unparsedFiles.forEach(file => {
                    unparsedFileIds.add(String(file.id));
                    const li = document.createElement('li');
                    li.textContent = file.path;
                    li.dataset.fileId = file.id;
                    li.addEventListener('click', () => {
                        elements.fileSelect.value = file.id;
                        handleFileSelection();
                        elements.unparsedFilesPopup.classList.add('hidden');
                    });
                    elements.unparsedFilesList.appendChild(li);
                });
            }
        } catch (error) {
            console.error("Failed to check project parsing status:", error);
            elements.projectParsingStatus.className = 'warning';
            elements.projectParsingSummary.textContent = 'Could not retrieve parsing status.';
        }
    }

    async function fetchFilesForProject(projectName) {
        try {
            setStatus(`Loading files for '${projectName}'...`, 'info');
            const response = await fetch(`/api/projects/${projectName}/files`);
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const files = await response.json();

            elements.fileSelect.innerHTML = '<option value="">-- Select a File --</option>';
            if (files.length > 0) {
                files.forEach(file => {
                    const option = document.createElement('option');
                    option.value = file.id;
                    option.textContent = file.path;
                    elements.fileSelect.appendChild(option);
                });
                elements.fileSelect.disabled = false;
                setStatus(`Loaded ${files.length} files for '${projectName}'.`, 'success');
                
                // Auto-select the first file
                elements.fileSelect.selectedIndex = 1;
                handleFileSelection();

            } else {
                elements.fileSelect.innerHTML = '<option>No parsed files found</option>';
                setStatus(`No parsed files found for '${projectName}'.`, 'info');
            }
            
            const hasMultipleFiles = files.length > 1;
            elements.prevFileBtn.disabled = !hasMultipleFiles;
            elements.nextFileBtn.disabled = !hasMultipleFiles;

        } catch (error) {
            console.error("Failed to fetch files:", error);
            elements.fileSelect.innerHTML = '<option>Error loading files</option>';
            setStatus('Error loading files.', 'error');
        }
    }

    async function handleFileSelection() {
        const selectedOption = elements.fileSelect.options[elements.fileSelect.selectedIndex];
        if (!selectedOption || !selectedOption.value) {
            currentFileDetails = null;
            elements.fileIssueIndicator.style.display = 'none';
            updateAllViews();
            return;
        }

        const fileId = selectedOption.value;
        if (unparsedFileIds.has(fileId)) {
            elements.fileIssueIndicator.style.display = 'inline';
        } else {
            elements.fileIssueIndicator.style.display = 'none';
        }

        try {
            setStatus(`Loading file details...`, 'info');
            const response = await fetch(`/api/files/${fileId}`);
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            
            currentFileDetails = await response.json();
            updateAllViews();
            setStatus(`File loaded successfully.`, 'success');

        } catch (error) {
            console.error("Failed to fetch file details:", error);
            setStatus('Error loading file details.', 'error');
            currentFileDetails = null;
            updateAllViews();
        }
    }

    async function parseProject() {
        const selectedOption = elements.projectSelect.options[elements.projectSelect.selectedIndex];
        if (!selectedOption || !selectedOption.value) return;

        const projectName = selectedOption.value;
        const projectPath = selectedOption.dataset.path;
        const isParsed = selectedOption.dataset.isParsed === 'true';

        const actionText = isParsed ? 'Re-parsing' : 'Parsing';
        setStatus(`Request to ${actionText.toLowerCase()} '${projectName}' sent... This may take a moment.`, 'info');
        elements.parseBtn.disabled = true;
        elements.parseBtn.textContent = `${actionText}...`;

        try {
            const payload = {
                name: projectName,
                path: projectPath
            };

            // For a re-parse, we send a flag to the backend.
            if (isParsed) {
                payload.reparse = true;
            }

            const response = await fetch('/api/projects/parse', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });

            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.detail || `Failed to ${actionText.toLowerCase()} project.`);
            }
            
            setStatus(result.message + " Refreshing project list...", 'info');
            setTimeout(fetchProjects, 3000); // Shortened delay

        } catch (error) {
            console.error("Failed to parse project:", error);
            setStatus(`Error: ${error.message}`, 'error');
            // Re-enable button but don't reset text, it will be updated on refresh
            elements.parseBtn.disabled = false; 
        }
    }

    function navigateTo(direction) {
        const numOptions = elements.fileSelect.options.length;
        if (numOptions <= 1) return; // No files to navigate

        let currentIndex = elements.fileSelect.selectedIndex;
        
        // The first option is "-- Select a File --", so valid indices are 1 to numOptions - 1
        let newIndex = currentIndex + direction;

        if (newIndex < 1) {
            newIndex = numOptions - 1; // Wrap around to the last file
        } else if (newIndex >= numOptions) {
            newIndex = 1; // Wrap around to the first file
        }
        
        elements.fileSelect.selectedIndex = newIndex;
        handleFileSelection();
    }

    function jumpToNextIssue() {
        const options = Array.from(elements.fileSelect.options);
        if (options.length <= 1 || unparsedFileIds.size === 0) return;

        let searchIndex = elements.fileSelect.selectedIndex + 1;

        for (let i = 0; i < options.length; i++) {
            if (searchIndex >= options.length) {
                searchIndex = 1; // Wrap around to the first actual file
            }
            
            const option = options[searchIndex];
            if (unparsedFileIds.has(option.value)) {
                elements.fileSelect.selectedIndex = searchIndex;
                handleFileSelection();
                return; 
            }
            
            searchIndex++;
        }
    }

    function handleTabClick(e) {
        const targetTab = e.currentTarget;
        
        elements.tabs.forEach(tab => tab.classList.remove('active'));
        elements.tabContents.forEach(content => content.classList.remove('active'));

        targetTab.classList.add('active');
        document.getElementById(targetTab.dataset.tab).classList.add('active');

        // Re-render the active view in case the window was resized
        updateAllViews(); 
    }

    function updateAllViews() {
        FileInspector.render(currentFileDetails);
        FileOverview.render(currentFileDetails);
        WorkflowOverview.render();
    }


    // --- EVENT LISTENERS ---
    elements.projectSelect.addEventListener('change', handleProjectSelection);
    elements.fileSelect.addEventListener('change', handleFileSelection);
    elements.prevFileBtn.addEventListener('click', () => navigateTo(-1));
    elements.nextFileBtn.addEventListener('click', () => navigateTo(1));
    elements.jumpToIssueBtn.addEventListener('click', jumpToNextIssue);
    elements.parseBtn.addEventListener('click', parseProject);
    elements.projectParsingSummary.addEventListener('click', () => {
        elements.unparsedFilesPopup.classList.toggle('hidden');
    });
    elements.unparsedFilesCloseBtn.addEventListener('click', () => {
        elements.unparsedFilesPopup.classList.add('hidden');
    });
    elements.tabs.forEach(tab => tab.addEventListener('click', handleTabClick));

    // --- INITIALIZATION ---
    FileInspector.init();
    FileOverview.init();
    WorkflowOverview.init();
    fetchProjects();
}); 