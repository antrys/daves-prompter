/**
 * Dave's Prompter - Frontend Application
 * Handles WebSocket connection, UI interactions, and auto-scrolling
 */

class SpeechPrompter {
    constructor() {
        // State
        this.isRunning = false;
        this.isMirrored = false;
        this.script = '';
        this.wordCount = 0;
        this.currentPosition = 0;
        this.fontSize = 48;
        this.scrollMargin = 30;
        this.scrollSpeed = 300;
        this.autoHideControls = false;
        this.hideControlsTimeout = null;

        // WebSocket
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;

        // Elements
        this.elements = {};

        // Initialize
        this.init();
    }

    init() {
        this.cacheElements();
        this.loadSettings();
        this.bindEvents();
        this.connectWebSocket();
        this.loadDevices();
    }

    cacheElements() {
        this.elements = {
            app: document.getElementById('app'),
            controlBar: document.getElementById('controlBar'),
            btnLoadScript: document.getElementById('btnLoadScript'),
            btnStartStop: document.getElementById('btnStartStop'),
            btnStartStopText: document.getElementById('btnStartStopText'),
            iconPlay: document.getElementById('iconPlay'),
            iconStop: document.getElementById('iconStop'),
            btnReset: document.getElementById('btnReset'),
            btnFontMinus: document.getElementById('btnFontMinus'),
            btnFontPlus: document.getElementById('btnFontPlus'),
            fontSizeDisplay: document.getElementById('fontSizeDisplay'),
            btnMirror: document.getElementById('btnMirror'),
            btnSettings: document.getElementById('btnSettings'),
            btnFullscreen: document.getElementById('btnFullscreen'),
            btnShutdown: document.getElementById('btnShutdown'),
            statusIndicator: document.getElementById('statusIndicator'),
            statusText: document.getElementById('statusText'),
            wordProgress: document.getElementById('wordProgress'),
            prompterContainer: document.getElementById('prompterContainer'),
            prompterContent: document.getElementById('prompterContent'),
            scriptDisplay: document.getElementById('scriptDisplay'),
            placeholderMessage: document.getElementById('placeholderMessage'),
            readingLine: document.getElementById('readingLine'),
            // Script modal
            scriptModal: document.getElementById('scriptModal'),
            modalBackdrop: document.getElementById('modalBackdrop'),
            btnCloseModal: document.getElementById('btnCloseModal'),
            scriptInput: document.getElementById('scriptInput'),
            dropZone: document.getElementById('dropZone'),
            btnCancelScript: document.getElementById('btnCancelScript'),
            btnConfirmScript: document.getElementById('btnConfirmScript'),
            // Settings modal
            settingsModal: document.getElementById('settingsModal'),
            settingsBackdrop: document.getElementById('settingsBackdrop'),
            btnCloseSettings: document.getElementById('btnCloseSettings'),
            audioDevice: document.getElementById('audioDevice'),
            scrollMargin: document.getElementById('scrollMargin'),
            scrollMarginValue: document.getElementById('scrollMarginValue'),
            scrollSpeed: document.getElementById('scrollSpeed'),
            scrollSpeedValue: document.getElementById('scrollSpeedValue'),
            autoHideControls: document.getElementById('autoHideControls'),
            btnSaveSettings: document.getElementById('btnSaveSettings'),
        };
    }

    bindEvents() {
        // Control buttons
        this.elements.btnLoadScript.addEventListener('click', () => this.openScriptModal());
        this.elements.btnStartStop.addEventListener('click', () => this.toggleRecognition());
        this.elements.btnReset.addEventListener('click', () => this.resetPosition());
        this.elements.btnFontMinus.addEventListener('click', () => this.adjustFontSize(-4));
        this.elements.btnFontPlus.addEventListener('click', () => this.adjustFontSize(4));
        this.elements.btnMirror.addEventListener('click', () => this.toggleMirror());
        this.elements.btnSettings.addEventListener('click', () => this.openSettingsModal());
        this.elements.btnFullscreen.addEventListener('click', () => this.toggleFullscreen());
        this.elements.btnShutdown.addEventListener('click', () => this.shutdownServer());

        // Script modal
        this.elements.modalBackdrop.addEventListener('click', () => this.closeScriptModal());
        this.elements.btnCloseModal.addEventListener('click', () => this.closeScriptModal());
        this.elements.btnCancelScript.addEventListener('click', () => this.closeScriptModal());
        this.elements.btnConfirmScript.addEventListener('click', () => this.loadScript());

        // Drop zone - also allow dropping on the textarea
        this.elements.dropZone.addEventListener('dragover', (e) => this.handleDragOver(e));
        this.elements.dropZone.addEventListener('dragleave', () => this.handleDragLeave());
        this.elements.dropZone.addEventListener('drop', (e) => this.handleDrop(e));

        // Also allow dropping on the textarea itself
        this.elements.scriptInput.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.stopPropagation();
        });
        this.elements.scriptInput.addEventListener('drop', (e) => this.handleDrop(e));

        // Settings modal
        this.elements.settingsBackdrop.addEventListener('click', () => this.closeSettingsModal());
        this.elements.btnCloseSettings.addEventListener('click', () => this.closeSettingsModal());
        this.elements.btnSaveSettings.addEventListener('click', () => this.saveSettings());

        // Settings inputs
        this.elements.scrollMargin.addEventListener('input', (e) => {
            this.elements.scrollMarginValue.textContent = `${e.target.value}%`;
        });
        this.elements.scrollSpeed.addEventListener('input', (e) => {
            this.elements.scrollSpeedValue.textContent = `${e.target.value}ms`;
        });

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => this.handleKeydown(e));

        // Auto-hide controls on mouse movement
        document.addEventListener('mousemove', () => this.handleMouseMove());
    }

    handleKeydown(e) {
        // Don't handle shortcuts when typing in inputs
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

        switch (e.key) {
            case ' ':
                e.preventDefault();
                this.toggleRecognition();
                break;
            case 'r':
            case 'R':
                this.resetPosition();
                break;
            case '+':
            case '=':
                this.adjustFontSize(4);
                break;
            case '-':
            case '_':
                this.adjustFontSize(-4);
                break;
            case 'm':
            case 'M':
                this.toggleMirror();
                break;
            case 'f':
            case 'F':
                this.toggleFullscreen();
                break;
            case 'Escape':
                this.closeScriptModal();
                this.closeSettingsModal();
                break;
        }
    }

    handleMouseMove() {
        if (!this.autoHideControls || !this.isRunning) return;

        this.elements.app.classList.remove('controls-hidden');

        clearTimeout(this.hideControlsTimeout);
        this.hideControlsTimeout = setTimeout(() => {
            if (this.isRunning && this.autoHideControls) {
                this.elements.app.classList.add('controls-hidden');
            }
        }, 3000);
    }

    // WebSocket
    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.reconnectAttempts = 0;
            this.updateStatus('Connected', false);
        };

        this.ws.onmessage = (event) => {
            const message = JSON.parse(event.data);
            this.handleWebSocketMessage(message);
        };

        this.ws.onclose = () => {
            console.log('WebSocket closed');
            this.updateStatus('Disconnected', true);
            this.attemptReconnect();
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.updateStatus('Error', true);
        };
    }

    attemptReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.log('Max reconnection attempts reached');
            return;
        }

        this.reconnectAttempts++;
        const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 10000);

        setTimeout(() => {
            console.log(`Attempting reconnection (${this.reconnectAttempts}/${this.maxReconnectAttempts})`);
            this.connectWebSocket();
        }, delay);
    }

    handleWebSocketMessage(message) {
        switch (message.type) {
            case 'init':
                this.isRunning = message.running;
                if (message.script) {
                    this.script = message.script;
                    this.wordCount = message.word_count;
                    this.renderScript();
                }
                this.currentPosition = message.position;
                this.updateUI();
                break;

            case 'script':
                this.script = message.text;
                this.wordCount = message.word_count;
                this.renderScript();
                break;

            case 'status':
                this.isRunning = message.running;
                this.updateUI();
                break;

            case 'match':
            case 'words':
                this.currentPosition = message.word_index;
                this.highlightWord(message.word_index, message.matched_words || []);
                this.scrollToWord(message.word_index);
                this.updateProgress();
                break;

            case 'partial':
                if (message.position !== null) {
                    this.highlightWord(message.position, [], true);
                }
                break;

            case 'position':
            case 'reset':
                this.currentPosition = message.position;
                this.highlightWord(message.position, []);
                this.scrollToWord(message.position);
                this.updateProgress();
                break;

            case 'pong':
                // Heartbeat response
                break;
        }
    }

    sendMessage(message) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
        }
    }

    // API calls
    async toggleRecognition() {
        const endpoint = this.isRunning ? '/api/stop' : '/api/start';

        try {
            const response = await fetch(endpoint, { method: 'POST' });
            const data = await response.json();

            if (response.ok) {
                this.isRunning = data.running;
                this.updateUI();
            } else {
                console.error('Failed to toggle recognition:', data.error);
                this.updateStatus('Error: ' + (data.error || 'Unknown error'), true);
            }
        } catch (error) {
            console.error('Error toggling recognition:', error);
            this.updateStatus('Connection error', true);
        }
    }

    async resetPosition() {
        try {
            await fetch('/api/reset', { method: 'POST' });
            // Reset all tracking
            this._lastScrolledIndex = undefined;
            this._lastGreyedIndex = -1;
            // Scroll to top
            this.elements.prompterContent.scrollTop = 0;
            // Reset all words to upcoming (white)
            const words = this.elements.scriptDisplay.querySelectorAll('.word');
            words.forEach(word => {
                word.classList.remove('spoken');
                word.classList.add('upcoming');
            });
        } catch (error) {
            console.error('Error resetting position:', error);
        }
    }

    async loadDevices() {
        try {
            const response = await fetch('/api/devices');
            const data = await response.json();

            if (data.devices) {
                this.elements.audioDevice.innerHTML = '';
                data.devices.forEach(device => {
                    const option = document.createElement('option');
                    option.value = device.index;
                    option.textContent = device.name;
                    this.elements.audioDevice.appendChild(option);
                });
            }
        } catch (error) {
            console.error('Error loading devices:', error);
            this.elements.audioDevice.innerHTML = '<option value="">Error loading devices</option>';
        }
    }

    // Script handling
    openScriptModal() {
        this.elements.scriptModal.classList.add('active');
        this.elements.scriptInput.value = this.script;
        this.elements.scriptInput.focus();
    }

    closeScriptModal() {
        this.elements.scriptModal.classList.remove('active');
    }

    async loadScript() {
        const text = this.elements.scriptInput.value.trim();
        if (!text) return;

        try {
            const response = await fetch('/api/script', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text })
            });

            if (response.ok) {
                this.closeScriptModal();
            }
        } catch (error) {
            console.error('Error loading script:', error);
        }
    }

    handleDragOver(e) {
        e.preventDefault();
        this.elements.dropZone.classList.add('dragover');
    }

    handleDragLeave() {
        this.elements.dropZone.classList.remove('dragover');
    }

    handleDrop(e) {
        e.preventDefault();
        e.stopPropagation();
        this.elements.dropZone.classList.remove('dragover');

        const files = e.dataTransfer.files;
        if (files.length > 0) {
            const file = files[0];
            const fileName = file.name.toLowerCase();

            if (fileName.endsWith('.odt')) {
                this.parseODTFile(file);
            } else if (file.type === 'text/plain' || fileName.endsWith('.txt')) {
                const reader = new FileReader();
                reader.onload = (evt) => {
                    this.elements.scriptInput.value = evt.target.result;
                };
                reader.readAsText(file);
            } else {
                alert('Please drop a .txt or .odt file');
            }
        }
    }

    async parseODTFile(file) {
        try {
            if (typeof JSZip === 'undefined') {
                throw new Error('JSZip library not loaded');
            }

            // ODT files are ZIP archives containing content.xml
            const zip = await JSZip.loadAsync(file);
            const contentFile = zip.file('content.xml');

            if (!contentFile) {
                throw new Error('content.xml not found in ODT file');
            }

            const contentXml = await contentFile.async('string');
            const parser = new DOMParser();
            const doc = parser.parseFromString(contentXml, 'application/xml');

            // Check for parse errors
            const parseError = doc.querySelector('parsererror');
            if (parseError) {
                throw new Error('XML parse error');
            }

            // Extract text from paragraphs, preserving structure
            const paragraphs = doc.getElementsByTagNameNS('urn:oasis:names:tc:opendocument:xmlns:text:1.0', 'p');
            let text = '';

            for (let i = 0; i < paragraphs.length; i++) {
                const para = paragraphs[i];
                let paraText = this.extractODTText(para);

                if (paraText.trim()) {
                    text += paraText + '\n\n';
                } else {
                    text += '\n';
                }
            }

            this.elements.scriptInput.value = text.trim();

        } catch (error) {
            console.error('Error parsing ODT file:', error);
            alert('Error reading ODT file: ' + error.message);
        }
    }

    extractODTText(element) {
        // Recursively extract text from ODT XML elements
        let text = '';

        for (const node of element.childNodes) {
            if (node.nodeType === Node.TEXT_NODE) {
                text += node.textContent;
            } else if (node.nodeType === Node.ELEMENT_NODE) {
                const localName = node.localName;

                if (localName === 'line-break' || localName === 'soft-page-break') {
                    text += '\n';
                } else if (localName === 'tab') {
                    text += '\t';
                } else if (localName === 's') {
                    // Space element - may have count attribute
                    const count = parseInt(node.getAttribute('text:c') || node.getAttribute('c') || '1', 10);
                    text += ' '.repeat(count);
                } else {
                    // Recurse into child elements (spans, etc.)
                    text += this.extractODTText(node);
                }
            }
        }

        return text;
    }

    renderScript() {
        if (!this.script) {
            this.elements.placeholderMessage.classList.remove('hidden');
            this.elements.scriptDisplay.classList.add('hidden');
            return;
        }

        this.elements.placeholderMessage.classList.add('hidden');
        this.elements.scriptDisplay.classList.remove('hidden');

        // Parse script into words and render, preserving line breaks
        const wordPattern = /[\w']+|[^\w\s]+|\s+/g;
        let match;
        let wordIndex = 0;
        let html = '';

        while ((match = wordPattern.exec(this.script)) !== null) {
            const token = match[0];

            if (/[\w']+/.test(token)) {
                // It's a word
                html += `<span class="word upcoming" data-index="${wordIndex}">${this.escapeHtml(token)}</span>`;
                wordIndex++;
            } else if (/\s/.test(token)) {
                // Whitespace - preserve line breaks
                // Convert \n to <br>, preserve multiple line breaks
                const withBreaks = token
                    .replace(/\r\n/g, '\n')  // Normalize line endings
                    .replace(/\n\n+/g, '<br><br>')  // Double+ newlines = paragraph break
                    .replace(/\n/g, '<br>')  // Single newline = line break
                    .replace(/ {2,}/g, (spaces) => '&nbsp;'.repeat(spaces.length));  // Preserve multiple spaces
                html += withBreaks || ' ';
            } else {
                // Punctuation
                html += this.escapeHtml(token);
            }
        }

        this.elements.scriptDisplay.innerHTML = html;
        this.updateProgress();
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    highlightWord(index, matchedWords = [], isPartial = false) {
        // Track the highest fragment we've completed
        // We grey out PREVIOUS fragments, not the current one
        if (this._lastGreyedIndex === undefined) {
            this._lastGreyedIndex = -1;
        }

        // Only process if we've moved forward significantly (at least 1 word)
        // This prevents greying out mid-sentence
        if (index <= this._lastGreyedIndex) {
            return;
        }

        // Grey out words UP TO (including the word just spoken)
        // The user wants immediate feedback that the word is "done"
        const greyUpTo = index;

        if (greyUpTo <= this._lastGreyedIndex) {
            return;
        }

        this._lastGreyedIndex = greyUpTo;

        const words = this.elements.scriptDisplay.querySelectorAll('.word');
        words.forEach((word, i) => {
            if (i <= greyUpTo) {
                word.classList.add('spoken');
                word.classList.remove('upcoming');
            }
        });
    }

    scrollToWord(index) {
        // Only scroll if the position has moved FORWARD
        if (this._lastScrolledIndex !== undefined && index <= this._lastScrolledIndex) {
            return; // Never scroll backward
        }
        this._lastScrolledIndex = index;

        const word = this.elements.scriptDisplay.querySelector(`[data-index="${index}"]`);
        if (!word) return;

        const container = this.elements.prompterContent;
        const containerRect = container.getBoundingClientRect();
        const wordRect = word.getBoundingClientRect();

        // Calculate where the word should be (at reading line)
        const readingLineY = containerRect.top + (containerRect.height * this.scrollMargin / 100);
        const wordCenterY = wordRect.top + wordRect.height / 2;
        const scrollDelta = wordCenterY - readingLineY;

        // Only scroll DOWN (forward), never up
        if (scrollDelta <= 0) {
            return;
        }

        // Only scroll if word is far enough from reading line
        if (scrollDelta < 30) {
            return;
        }

        // Calculate target scroll position
        const targetScroll = container.scrollTop + scrollDelta;

        // Smoothly animate to target (800ms = snappy but smooth)
        this._smoothScrollTo(container, targetScroll, 800);
    }

    _smoothScrollTo(element, target, duration) {
        // Cancel any existing scroll animation
        if (this._scrollAnimation) {
            cancelAnimationFrame(this._scrollAnimation);
        }

        const start = element.scrollTop;
        const distance = target - start;

        // Limit scroll speed: ~400 pixels per second (was ~150)
        // This ensures consistent speed regardless of distance
        const minDuration = Math.abs(distance) * 2.5; // 2.5ms per pixel = ~400px/sec
        const actualDuration = Math.max(duration, minDuration);

        const startTime = performance.now();

        const animateScroll = (currentTime) => {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / actualDuration, 1);

            // More linear easing - gentle and consistent speed
            // Using sine ease-in-out for smooth start and end
            const ease = 0.5 - 0.5 * Math.cos(progress * Math.PI);

            element.scrollTop = start + distance * ease;

            if (progress < 1) {
                this._scrollAnimation = requestAnimationFrame(animateScroll);
            }
        };

        this._scrollAnimation = requestAnimationFrame(animateScroll);
    }

    // UI updates
    updateUI() {
        // Start/Stop button
        if (this.isRunning) {
            this.elements.btnStartStop.classList.add('active');
            this.elements.btnStartStopText.textContent = 'Stop';
            this.elements.iconPlay.style.display = 'none';
            this.elements.iconStop.style.display = 'block';
            this.elements.statusIndicator.classList.add('listening');
            this.elements.statusIndicator.classList.remove('error');
            this.elements.statusText.textContent = 'Listening...';
        } else {
            this.elements.btnStartStop.classList.remove('active');
            this.elements.btnStartStopText.textContent = 'Start';
            this.elements.iconPlay.style.display = 'block';
            this.elements.iconStop.style.display = 'none';
            this.elements.statusIndicator.classList.remove('listening');
            this.elements.statusText.textContent = 'Ready';
            this.elements.app.classList.remove('controls-hidden');
        }
    }

    updateStatus(text, isError = false) {
        this.elements.statusText.textContent = text;
        this.elements.statusIndicator.classList.toggle('error', isError);
    }

    updateProgress() {
        this.elements.wordProgress.textContent = `${this.currentPosition + 1} / ${this.wordCount}`;
    }

    adjustFontSize(delta) {
        this.fontSize = Math.max(24, Math.min(120, this.fontSize + delta));
        document.documentElement.style.setProperty('--font-size-prompter', `${this.fontSize}px`);
        this.elements.fontSizeDisplay.textContent = `${this.fontSize}px`;
        this.saveSettings();
    }

    toggleMirror() {
        this.isMirrored = !this.isMirrored;
        this.elements.app.classList.toggle('mirrored', this.isMirrored);
        this.elements.btnMirror.classList.toggle('active', this.isMirrored);
        this.saveSettings();
    }

    toggleFullscreen() {
        if (document.fullscreenElement) {
            document.exitFullscreen();
        } else {
            document.documentElement.requestFullscreen();
        }
    }

    async shutdownServer() {
        if (!confirm('Are you sure you want to shutdown the server?')) {
            return;
        }

        try {
            this.updateStatus('Shutting down...', false);
            const response = await fetch('/api/shutdown', { method: 'POST' });

            if (response.ok) {
                this.updateStatus('Server stopped', false);
                // Show message that server is down
                setTimeout(() => {
                    document.body.innerHTML = `
                        <div style="display: flex; align-items: center; justify-content: center; height: 100vh; background: #1a1b26; color: #a0a0a0; font-family: sans-serif; flex-direction: column; gap: 1rem;">
                            <h2 style="color: #f0f0f0;">Server Stopped</h2>
                            <p>The prompter server has been shut down.</p>
                            <p style="font-size: 0.9em;">Run <code style="background: #2a2b32; padding: 0.2em 0.5em; border-radius: 4px;">./start.sh</code> to restart.</p>
                        </div>
                    `;
                }, 500);
            }
        } catch (error) {
            console.error('Error shutting down:', error);
        }
    }

    // Settings
    openSettingsModal() {
        this.elements.settingsModal.classList.add('active');
        this.elements.scrollMargin.value = this.scrollMargin;
        this.elements.scrollMarginValue.textContent = `${this.scrollMargin}%`;
        this.elements.scrollSpeed.value = this.scrollSpeed;
        this.elements.scrollSpeedValue.textContent = `${this.scrollSpeed}ms`;
        this.elements.autoHideControls.checked = this.autoHideControls;
    }

    closeSettingsModal() {
        this.elements.settingsModal.classList.remove('active');
    }

    loadSettings() {
        const settings = JSON.parse(localStorage.getItem('prompterSettings') || '{}');

        this.fontSize = settings.fontSize || 48;
        this.isMirrored = settings.isMirrored || false;
        this.scrollMargin = settings.scrollMargin || 30;
        this.scrollSpeed = settings.scrollSpeed || 300;
        this.autoHideControls = settings.autoHideControls || false;

        // Apply settings
        document.documentElement.style.setProperty('--font-size-prompter', `${this.fontSize}px`);
        document.documentElement.style.setProperty('--reading-line-position', `${this.scrollMargin}%`);
        document.documentElement.style.setProperty('--scroll-duration', `${this.scrollSpeed}ms`);

        this.elements.fontSizeDisplay.textContent = `${this.fontSize}px`;
        this.elements.app.classList.toggle('mirrored', this.isMirrored);
    }

    saveSettings() {
        // Get values from inputs if settings modal is open
        if (this.elements.settingsModal.classList.contains('active')) {
            this.scrollMargin = parseInt(this.elements.scrollMargin.value, 10);
            this.scrollSpeed = parseInt(this.elements.scrollSpeed.value, 10);
            this.autoHideControls = this.elements.autoHideControls.checked;

            // Update audio device
            const deviceIndex = this.elements.audioDevice.value;
            if (deviceIndex !== '') {
                fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ device_index: parseInt(deviceIndex, 10) })
                });
            }
        }

        // Apply CSS variables
        document.documentElement.style.setProperty('--reading-line-position', `${this.scrollMargin}%`);
        document.documentElement.style.setProperty('--scroll-duration', `${this.scrollSpeed}ms`);

        // Save to localStorage
        const settings = {
            fontSize: this.fontSize,
            isMirrored: this.isMirrored,
            scrollMargin: this.scrollMargin,
            scrollSpeed: this.scrollSpeed,
            autoHideControls: this.autoHideControls
        };

        localStorage.setItem('prompterSettings', JSON.stringify(settings));

        this.closeSettingsModal();
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.prompter = new SpeechPrompter();
});



