// ─── State ──────────────────────────────────────────────
const API_BASE = window.location.origin;
let currentMode = 'explain';

// Configure Marked.js with Highlight.js
marked.setOptions({
    highlight: function(code, lang) {
        const language = hljs.getLanguage(lang) ? lang : 'plaintext';
        return hljs.highlight(code, { language }).value;
    },
    langPrefix: 'hljs language-'
});

// ─── Mode Selection ─────────────────────────────────────
function setMode(mode, btn) {
    currentMode = mode;
    document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    // Update placeholder based on mode
    const placeholders = {
        explain: "Ask about architecture, design patterns, how components connect...",
        eli5: "Ask anything — I'll explain it like you're 5! 🎈",
        bugs: "Ask about potential bugs, edge cases, or improvements...",
        search: "Search for specific functions, classes, or concepts..."
    };
    document.getElementById('questionInput').placeholder = placeholders[mode] || '';
    document.getElementById('questionInput').focus();
}

// ─── Status Bar ─────────────────────────────────────────
function showStatus(message, type = 'loading') {
    const bar = document.getElementById('statusBar');
    const msgSpan = document.getElementById('statusMsg');
    const iconSpan = document.getElementById('statusIcon');
    
    bar.className = `status-bar show status-${type}`;
    
    // Icons from Boxicons (or emoji if boxicons fails to load)
    const icons = {
        loading: '<i class="bx bx-loader-alt bx-spin"></i>',
        success: '<i class="bx bxs-check-circle"></i>',
        error: '<i class="bx bxs-error-circle"></i>'
    };
    
    if (iconSpan) iconSpan.innerHTML = icons[type] || '';
    if (msgSpan) msgSpan.textContent = message;
}

function hideStatus() {
    const bar = document.getElementById('statusBar');
    if(bar) bar.className = 'status-bar';
}

// ─── Ingest Repository ──────────────────────────────────
async function ingestRepo() {
    const input = document.getElementById('repoInput');
    const btn = document.getElementById('ingestBtn');
    const btnText = document.getElementById('ingestBtnText');
    const url = input.value.trim();

    if (!url) {
        showStatus('Please enter a GitHub repository URL.', 'error');
        return;
    }

    if (!url.startsWith('https://github.com/')) {
        showStatus('Invalid URL format. Requires https://github.com/...', 'error');
        return;
    }

    // Set loading state
    btn.disabled = true;
    const originalText = btnText.innerHTML;
    btnText.innerHTML = '<div class="spinner"></div> Ingesting...';
    showStatus('Cloning and indexing code chunks into Endee... please wait', 'loading');

    try {
        const response = await fetch(`${API_BASE}/ingest`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repo_url: url }),
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Ingestion failed');
        }

        showStatus(`${data.message} (${data.chunks_indexed} chunks indexed)`, 'success');

        // Allow asking right away
        document.getElementById('questionInput').focus();

    } catch (err) {
        showStatus(`Error: ${err.message}`, 'error');
    } finally {
        btn.disabled = false;
        btnText.innerHTML = originalText;
    }
}

// ─── Ask Question ───────────────────────────────────────
async function askQuestion() {
    const input = document.getElementById('questionInput');
    const btn = document.getElementById('askBtn');
    const question = input.value.trim();

    if (!question) return;

    // Hide empty state
    const emptyState = document.getElementById('chatEmpty');
    if (emptyState) emptyState.style.display = 'none';

    // Add user message
    addMessage(question, 'user');
    input.value = '';

    // Show thinking animation
    const thinkingId = addThinking();

    // Disable input while generating
    btn.disabled = true;
    input.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/ask`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question, mode: currentMode }),
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Failed to get answer');
        }

        removeThinking(thinkingId);
        addMessage(data.answer, 'ai', data.sources || []);

    } catch (err) {
        removeThinking(thinkingId);
        addMessage(`❌ Error: ${err.message}`, 'ai', []);
    } finally {
        btn.disabled = false;
        input.disabled = false;
        input.focus();
    }
}

// ─── DOM Message Rendering ────────────────────────────────
function addMessage(content, type, sources = []) {
    const container = document.getElementById('chatContainer');

    const div = document.createElement('div');
    div.className = `message message-${type}`;

    if (type === 'user') {
        div.innerHTML = `<div class="bubble">${escapeHtml(content)}</div>`;
    } else {
        const modeLabels = {
            explain: 'Architecture',
            eli5: 'ELI5',
            bugs: 'Bug Finder',
            search: 'Code Search'
        };
        const modeIcons = {
            explain: '<i class="bx bx-building"></i>',
            eli5: '<i class="bx bx-smile"></i>',
            bugs: '<i class="bx bx-bug"></i>',
            search: '<i class="bx bx-search"></i>'
        };

        const parsedContent = marked.parse(content);

        let html = `
            <div class="ai-header">
                <div class="ai-avatar"><i class='bx bxl-codepen'></i></div>
                ${modeIcons[currentMode]} ${modeLabels[currentMode] || 'AI'}
            </div>
            <div class="message-content">${parsedContent}</div>
        `;

        if (sources && sources.length > 0) {
            html += '<div class="sources-container">';
            for (const src of sources) {
                html += `
                    <div class="source-chip" title="${escapeHtml(src.file)}">
                        <i class='bx bx-file'></i>
                        ${escapeHtml(src.file.split('/').pop())}:${src.start_line}
                        <span class="chip-score">${(src.similarity * 100).toFixed(1)}%</span>
                    </div>
                `;
            }
            html += '</div>';
        }

        div.innerHTML = html;
    }

    container.appendChild(div);
    scrollToBottom();
}

function addThinking() {
    const container = document.getElementById('chatContainer');
    const id = 'thinking-' + Date.now();
    const div = document.createElement('div');
    div.id = id;
    div.className = 'message message-ai';
    
    div.innerHTML = `
        <div class="ai-header">
            <div class="ai-avatar"><i class='bx bxl-codepen bx-spin'></i></div>
            Analyzing Context
        </div>
        <div class="thinking-box">
            <div class="dots"><span></span><span></span><span></span></div>
            Consulting vector store & generating answer...
        </div>
    `;
    
    container.appendChild(div);
    scrollToBottom();
    return id;
}

function removeThinking(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

function scrollToBottom() {
    const container = document.getElementById('chatContainer');
    container.scrollTop = container.scrollHeight;
}

function escapeHtml(unsafe) {
    return unsafe
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
}

// ─── Keyboard Shortcuts ─────────────────────────────────
document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        const repoInput = document.getElementById('repoInput');
        if (document.activeElement === repoInput) {
            ingestRepo();
        }
    }
});

// ─── Check Status on Load ───────────────────────────────
window.addEventListener('DOMContentLoaded', async () => {
    try {
        const res = await fetch(`${API_BASE}/status`);
        const data = await res.json();
        if (data.status === 'connected') {
            console.log('✅ Endee connected:', data);
        }
    } catch (e) {
        console.log('⚠️ Backend not reachable yet');
    }
});
