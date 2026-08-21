document.addEventListener('DOMContentLoaded', () => {
    const timerElement = document.getElementById('examTimer');
    const warningCountElement = document.getElementById('warningCount');
    const cameraStream = document.getElementById('cameraStream');
    const aiStatusBadge = document.getElementById('aiStatusBadge');
    const startButton = document.getElementById('startExamButton');
    const stopButton = document.getElementById('stopExamButton');
    const generateButton = document.getElementById('generateReportButton');
    const downloadButton = document.getElementById('downloadReportButton');
    const examStateMessage = document.getElementById('examStateMessage');

    let timerInterval = null;
    let elapsedSeconds = 0;
    let examRunning = false;
    let statusInterval = null;

    function setCameraVisible(visible) {
        if (!cameraStream) {
            return;
        }

        cameraStream.hidden = !visible;
        cameraStream.style.display = visible ? 'block' : 'none';
    }

    function formatTime(totalSeconds) {
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;

        return [hours, minutes, seconds]
            .map((value) => String(value).padStart(2, '0'))
            .join(':');
    }

    function renderTimer() {
        timerElement.textContent = formatTime(elapsedSeconds);
    }

    function setButtonState(running) {
        startButton.disabled = running;
        stopButton.disabled = !running;
        generateButton.disabled = running || elapsedSeconds === 0;
        downloadButton.disabled = running || elapsedSeconds === 0;
    }

    function setAiStatus(statusText) {
        if (!aiStatusBadge) {
            return;
        }

        aiStatusBadge.textContent = statusText;
        aiStatusBadge.classList.remove('status-neutral', 'status-alert');
        aiStatusBadge.classList.add(statusText === 'Face Not Detected' ? 'status-alert' : 'status-neutral');
    }

    async function pollAiStatus() {
        try {
            const response = await fetch('/exam/status', { cache: 'no-store' });
            if (!response.ok) {
                throw new Error('status request failed');
            }

            const data = await response.json();
            setAiStatus(data.ai_status || 'Monitoring');
        } catch (error) {
            setAiStatus('Monitoring');
        }
    }

    function startTimer() {
        if (examRunning) {
            return;
        }

        examRunning = true;
        examStateMessage.textContent = 'Exam is in progress.';
        setButtonState(true);

        if (cameraStream) {
            setCameraVisible(true);
            if (!cameraStream.getAttribute('src')) {
                cameraStream.src = '/video_feed';
            }
        }

        timerInterval = window.setInterval(() => {
            elapsedSeconds += 1;
            renderTimer();
        }, 1000);
    }

    function stopTimer() {
        if (!examRunning) {
            return;
        }

        examRunning = false;
        if (timerInterval !== null) {
            window.clearInterval(timerInterval);
            timerInterval = null;
        }

        fetch('/stop_video_feed', { method: 'POST', keepalive: true }).catch(() => {});

        if (cameraStream) {
            setCameraVisible(false);
            cameraStream.removeAttribute('src');
        }

        examStateMessage.textContent = 'Exam stopped. Reports are now available.';
        setButtonState(false);
    }

    function resetReportButtons() {
        generateButton.disabled = true;
        downloadButton.disabled = true;
    }

    startButton.addEventListener('click', () => {
        if (!examRunning) {
            if (elapsedSeconds === 0) {
                warningCountElement.textContent = warningCountElement.textContent.trim();
            }
            renderTimer();
            resetReportButtons();
            startTimer();
        }
    });

    stopButton.addEventListener('click', () => {
        stopTimer();
    });

    generateButton.addEventListener('click', () => {
        if (!generateButton.disabled) {
            examStateMessage.textContent = 'Report generation will be connected in the next step.';
        }
    });

    downloadButton.addEventListener('click', () => {
        if (!downloadButton.disabled) {
            examStateMessage.textContent = 'Download will be connected in the next step.';
        }
    });

    renderTimer();
    setButtonState(false);
    setCameraVisible(false);
    setAiStatus(aiStatusBadge ? aiStatusBadge.textContent.trim() : 'Monitoring');
    pollAiStatus();
    statusInterval = window.setInterval(pollAiStatus, 1000);

    window.addEventListener('beforeunload', () => {
        if (statusInterval !== null) {
            window.clearInterval(statusInterval);
        }
    });
});