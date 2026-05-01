/**
 * JARVIS Dashboard Logic
 * Handles real-time updates via SocketIO and Chart.js rendering.
 */

class JarvisDashboard {
    constructor() {
        this.socket = null;
        this.charts = {};
        this.startTime = Date.now();
        this.colors = {
            cyan: '#00d4ff',
            orange: '#ff6600',
            red: '#ff3e3e',
            green: '#00ff9d',
            gray: '#888888',
            teal: '#008080',
            yellow: '#ffd700'
        };
        this.moodColors = {
            neutral: this.colors.gray,
            engaged: this.colors.cyan,
            frustrated: this.colors.red,
            excited: this.colors.green,
            impatient: this.colors.orange,
            confused: this.colors.yellow,
            satisfied: this.colors.teal
        };
    }

    init() {
        this.initSocket();
        this.initTime();
        this.loadAllData();
        this.initForms();

        // Intervals
        setInterval(() => this.updateTime(), 1000);
        setInterval(() => this.loadAllData(), 60000); // Full refresh fallback
    }

    initSocket() {
        this.socket = io();

        this.socket.on('connect', () => {
            console.log('Connected to JARVIS');
            document.querySelector('.dot').style.backgroundColor = '#00ff9d';
        });

        this.socket.on('disconnect', () => {
            console.log('Disconnected from JARVIS');
            document.querySelector('.dot').style.backgroundColor = '#ff3e3e';
        });

        this.socket.on('system_stats', (data) => this.handleSystemStats(data));
        this.socket.on('mood_update', (data) => this.handleMoodUpdate(data));
        this.socket.on('autonomous_action', (data) => this.handleAutonomousAction(data));
        this.socket.on('performance_update', (data) => this.handlePerformanceUpdate(data));
        this.socket.on('jarvis_response', (data) => this.handleJarvisResponse(data));
    }

    initTime() {
        this.updateTime();
    }

    updateTime() {
        const now = new Date();
        document.getElementById('current-time').textContent = now.toLocaleTimeString();
    }

    async loadAllData() {
        try {
            const [convs, tools, resTimes, agents, lessons, tasks, moodHist, sentiment] = await Promise.all([
                fetch('/api/stats/conversations').then(r => r.json()),
                fetch('/api/stats/tools').then(r => r.json()),
                fetch('/api/stats/response-times').then(r => r.json()),
                fetch('/api/stats/agents').then(r => r.json()),
                fetch('/api/lessons').then(r => r.json()),
                fetch('/api/schedule').then(r => r.json()),
                fetch('/api/mood/history').then(r => r.json()),
                fetch('/api/mood/sentiment-breakdown').then(r => r.json())
            ]);

            this.renderInsights(convs);
            this.renderToolsChart(tools);
            this.renderResponseTimeChart(resTimes);
            this.renderAgentChart(agents);
            this.renderLessons(lessons);
            this.renderTasksTable(tasks.tasks || []);
            this.renderMoodTimeline(moodHist);
            this.renderSentimentDoughnut(sentiment);
            
            // Mock score data for initial load if none exists
            this.renderScoreChart([7, 8, 7, 9, 8, 7, 8, 9, 10, 9]);

            // Fetch sessions separately to not block main stats
            fetch('/api/sessions').then(r => r.json()).then(data => this.renderSessionsTable(data.sessions || []));

        } catch (err) {
            console.error('Failed to load dashboard data:', err);
        }
    }

    initForms() {
        const form = document.getElementById('add-task-form');
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const data = {
                name: document.getElementById('task-name').value,
                schedule: document.getElementById('task-schedule').value,
                action: document.getElementById('task-action').value
            };
            try {
                const res = await fetch('/api/schedule', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                }).then(r => r.json());
                if (res.success) {
                    form.reset();
                    this.loadAllData();
                }
            } catch (err) {
                console.error('Failed to add task:', err);
            }
        });
    }

    // --- HANDLERS ---

    handleSystemStats(data) {
        if (data.error) return;
        this.renderRing('cpu-ring', data.cpu_percent || 0, 100);
        this.renderRing('ram-ring', data.ram_percent || 0, 100);
        this.renderRing('disk-ring', data.disk_percent || 0, 100);
        document.getElementById('uptime-val').textContent = this.formatUptime(data.uptime_seconds || 0);
        if (data.battery_percent) {
            document.getElementById('battery-val').textContent = data.battery_percent + '%';
        }
        if (data.active_provider) {
            document.getElementById('active-provider').textContent = data.active_provider.toUpperCase();
        }
    }

    handleMoodUpdate(data) {
        this.renderMoodBadge(data.mood);
        // Refresh timeline
        fetch('/api/mood/history').then(r => r.json()).then(hist => this.renderMoodTimeline(hist));
    }

    handleAutonomousAction(data) {
        this.appendActivityEntry(data);
    }

    handlePerformanceUpdate(data) {
        document.getElementById('avg-score').textContent = (data.avg_score || 0).toFixed(1);
        const trend = document.getElementById('improvement-trend');
        trend.className = 'trend-' + (data.trend || 'stable');
        // If score chart exists, update it? (Logic for real-time score updates)
    }

    handleJarvisResponse(data) {
        // Refresh insights and response time chart
        fetch('/api/stats/conversations').then(r => r.json()).then(d => this.renderInsights(d));
        fetch('/api/stats/response-times').then(r => r.json()).then(d => this.renderResponseTimeChart(d));
    }

    // --- RENDERING ---

    renderRing(canvasId, value, max) {
        const canvas = document.getElementById(canvasId);
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const centerX = canvas.width / 2;
        const centerY = canvas.height / 2;
        const radius = 50;
        const percent = value / max;

        // Determine color
        let color = this.colors.cyan;
        if (value > 80) color = this.colors.red;
        else if (value > 60) color = this.colors.orange;

        ctx.clearRect(0, 0, canvas.width, canvas.height);

        // Track
        ctx.beginPath();
        ctx.arc(centerX, centerY, radius, 0, 2 * Math.PI);
        ctx.strokeStyle = 'rgba(255,255,255,0.05)';
        ctx.lineWidth = 8;
        ctx.stroke();

        // Progress
        ctx.beginPath();
        ctx.arc(centerX, centerY, radius, -Math.PI / 2, (-Math.PI / 2) + (2 * Math.PI * percent));
        ctx.strokeStyle = color;
        ctx.lineWidth = 8;
        ctx.lineCap = 'round';
        ctx.stroke();

        // Text
        ctx.fillStyle = '#fff';
        ctx.font = 'bold 18px monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(Math.round(value) + '%', centerX, centerY);
    }

    renderScoreChart(scores) {
        const ctx = document.getElementById('score-chart').getContext('2d');
        if (this.charts.score) this.charts.score.destroy();

        this.charts.score = new Chart(ctx, {
            type: 'line',
            data: {
                labels: scores.map((_, i) => i + 1),
                datasets: [{
                    label: 'Response Quality',
                    data: scores,
                    borderColor: this.colors.cyan,
                    backgroundColor: 'rgba(0, 212, 255, 0.1)',
                    borderWidth: 2,
                    tension: 0.4,
                    pointBackgroundColor: this.colors.orange,
                    pointRadius: 3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    y: { min: 0, max: 10, grid: { color: 'rgba(0,212,255,0.1)' }, ticks: { color: '#888' } },
                    x: { grid: { display: false }, ticks: { color: '#888' } }
                }
            }
        });
    }

    renderAgentChart(agentData) {
        const ctx = document.getElementById('agent-chart').getContext('2d');
        if (this.charts.agent) this.charts.agent.destroy();

        const labels = Object.keys(agentData);
        const data = labels.map(l => agentData[l].calls);

        this.charts.agent = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    data: data,
                    backgroundColor: 'rgba(0, 212, 255, 0.5)',
                    borderColor: this.colors.cyan,
                    borderWidth: 1
                }]
            },
            options: {
                indexAxis: 'y',
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { grid: { color: 'rgba(0,212,255,0.1)' }, ticks: { color: '#888' } },
                    y: { ticks: { color: '#888' } }
                }
            }
        });
    }

    renderSentimentDoughnut(data) {
        const ctx = document.getElementById('sentiment-chart').getContext('2d');
        if (this.charts.sentiment) this.charts.sentiment.destroy();

        const labels = Object.keys(data);
        const values = labels.map(l => data[l]);

        this.charts.sentiment = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: [
                        this.colors.green, this.colors.red, this.colors.gray, 
                        this.colors.orange, this.colors.cyan, this.colors.yellow, '#663399'
                    ],
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'right', labels: { color: '#888', font: { size: 10 } } }
                },
                cutout: '70%'
            }
        });
    }

    renderToolsChart(counts) {
        const ctx = document.getElementById('tools-chart').getContext('2d');
        if (this.charts.tools) this.charts.tools.destroy();

        const labels = Object.keys(counts);
        const data = labels.map(l => counts[l]);

        this.charts.tools = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    data: data,
                    backgroundColor: 'rgba(255, 102, 0, 0.5)',
                    borderColor: this.colors.orange,
                    borderWidth: 1
                }]
            },
            options: {
                indexAxis: 'y',
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { grid: { color: 'rgba(0,212,255,0.1)' }, ticks: { color: '#888' } },
                    y: { ticks: { color: '#888' } }
                }
            }
        });
    }

    renderResponseTimeChart(buckets) {
        const ctx = document.getElementById('response-time-chart').getContext('2d');
        if (this.charts.resTime) this.charts.resTime.destroy();

        const labels = Object.keys(buckets);
        const data = labels.map(l => buckets[l]);

        this.charts.resTime = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    data: data,
                    backgroundColor: 'rgba(0, 212, 255, 0.5)',
                    borderColor: this.colors.cyan,
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    y: { grid: { color: 'rgba(0,212,255,0.1)' }, ticks: { color: '#888' } },
                    x: { ticks: { color: '#888' } }
                }
            }
        });
    }

    renderMoodBadge(mood) {
        const badge = document.getElementById('current-mood-badge');
        badge.textContent = mood.toUpperCase();
        badge.className = 'mood-' + mood;
    }

    renderMoodTimeline(history) {
        const container = document.getElementById('mood-timeline');
        container.innerHTML = '';
        
        let positiveCount = 0;
        history.forEach(entry => {
            const mood = entry.mood || 'neutral';
            const dot = document.createElement('div');
            dot.className = 'mood-dot';
            dot.style.backgroundColor = this.moodColors[mood] || this.colors.gray;
            dot.title = `${mood} (${new Date(entry.timestamp * 1000).toLocaleTimeString()})`;
            container.appendChild(dot);

            if (['engaged', 'excited', 'satisfied'].includes(mood)) positiveCount++;
        });

        // Update satisfaction meter
        const score = history.length ? Math.round((positiveCount / history.length) * 100) : 0;
        document.getElementById('satisfaction-fill').style.width = score + '%';
        document.getElementById('satisfaction-val').textContent = score + '%';
    }

    renderInsights(data) {
        document.getElementById('total-sessions').textContent = data.total_conversations || 0;
        document.getElementById('total-messages').textContent = data.total_messages || 0;
        document.getElementById('avg-response').textContent = (data.avg_response_time_ms || 0) + 'ms';
        document.getElementById('top-tool').textContent = (data.most_used_tool || 'NONE').toUpperCase();
        document.getElementById('total-memories').textContent = data.total_memories || 0;
        document.getElementById('uptime-val').textContent = this.formatUptime(data.uptime || 0);
    }

    renderLessons(lessons) {
        const container = document.getElementById('recent-lessons-container');
        container.innerHTML = '';
        lessons.forEach(l => {
            const div = document.createElement('div');
            div.className = 'lesson-entry';
            div.innerHTML = `<strong>${l.pattern || 'Unknown'}</strong>: ${l.hint || 'No hint'}`;
            div.onclick = () => alert(`Full Lesson:\n\nPattern: ${l.pattern}\nFailure: ${l.failure}\nHint: ${l.hint}`);
            container.appendChild(div);
        });
    }

    renderTasksTable(tasks) {
        const tbody = document.querySelector('#tasks-table tbody');
        tbody.innerHTML = '';
        tasks.forEach(t => {
            const tr = document.createElement('tr');
            const enabled = t.enabled !== false;
            tr.innerHTML = `
                <td>${t.name}</td>
                <td>${t.cron_expression || t.schedule_str || 'N/A'}</td>
                <td>
                    <button class="btn-run" onclick="dashboard.runTask('${t.task_id}')">RUN</button>
                    <input type="checkbox" ${enabled ? 'checked' : ''} onchange="dashboard.toggleTask('${t.task_id}', this.checked)">
                </td>
            `;
            tbody.appendChild(tr);
        });
    }

    async toggleTask(id, enabled) {
        await fetch(`/api/schedule/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled })
        });
    }

    async runTask(id) {
        const res = await fetch(`/api/schedule/${id}/run`, { method: 'POST' }).then(r => r.json());
        this.appendActivityEntry({
            timestamp: Date.now() / 1000,
            action: 'Manual Run',
            description: `Triggered task ${id}: ${res.success ? 'Success' : 'Failed'}`
        });
    }

    renderSessionsTable(sessions) {
        const tbody = document.querySelector('#sessions-table tbody');
        tbody.innerHTML = '';
        sessions.slice(0, 10).forEach(s => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${new Date(s.start_time * 1000).toLocaleString()}</td>
                <td>${s.message_count || 0}</td>
                <td><span class="mood-dot" style="background:${this.moodColors[s.dominant_mood] || '#888'}; display:inline-block"></span></td>
                <td><button class="btn-load" onclick="dashboard.loadSession('${s.session_id}')">LOAD</button></td>
            `;
            tbody.appendChild(tr);
        });
    }

    async loadSession(id) {
        const res = await fetch(`/api/sessions/${id}/load`, { method: 'POST' }).then(r => r.json());
        if (res.ok) window.location.href = '/';
    }

    appendActivityEntry(data) {
        const container = document.getElementById('activity-feed');
        const div = document.createElement('div');
        div.className = 'feed-entry';
        const time = new Date((data.timestamp || Date.now() / 1000) * 1000).toLocaleTimeString();
        div.innerHTML = `
            <span class="time">[${time}]</span>
            <span class="desc">${data.description || data.action || 'Unknown action'}</span>
        `;
        container.prepend(div);
        if (container.children.length > 50) container.removeChild(container.lastChild);
    }

    formatUptime(s) {
        if (s < 60) return s + 's';
        if (s < 3600) return Math.floor(s/60) + 'm ' + (s%60) + 's';
        const h = Math.floor(s/3600);
        const m = Math.floor((s%3600)/60);
        return h + 'h ' + m + 'm';
    }
}

const dashboard = new JarvisDashboard();
document.addEventListener('DOMContentLoaded', () => dashboard.init());
