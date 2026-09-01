document.addEventListener('DOMContentLoaded', () => {
    const timerElement = document.getElementById('examTimer');
    const warningCountElement = document.getElementById('warningCount');
    const warningDetailMessage = document.getElementById('warningDetailMessage');
    const cameraStream = document.getElementById('cameraStream');
    const cameraPlaceholder = document.getElementById('cameraPlaceholder');
    const cameraStatusBadge = document.getElementById('cameraStatusBadge');
    const aiStatusBadge = document.getElementById('aiStatusBadge');
    const liveAlertBanner = document.getElementById('liveAlertBanner');
    const alertMessage = document.getElementById('alertMessage');
    const startButton = document.getElementById('startExamButton');
    const stopButton = document.getElementById('stopExamButton');
    const generateButton = document.getElementById('generateReportButton');
    const downloadButton = document.getElementById('downloadReportButton');
    const examStateMessage = document.getElementById('examStateMessage');

    // Checklist elements
    const checkFace = document.getElementById('checkFace');
    const valFace = document.getElementById('valFace');
    const checkPose = document.getElementById('checkPose');
    const valPose = document.getElementById('valPose');
    const checkPerson = document.getElementById('checkPerson');
    const valPerson = document.getElementById('valPerson');
    const checkPhone = document.getElementById('checkPhone');
    const valPhone = document.getElementById('valPhone');

    // Subsystem elements
    const sysCamera = document.getElementById('sysCamera');
    const sysFace = document.getElementById('sysFace');
    const sysPose = document.getElementById('sysPose');
    const sysYolo = document.getElementById('sysYolo');

    let timerInterval = null;
    let elapsedSeconds = 0;
    let examRunning = false;
    let statusInterval = null;
    let lastSessionId = null;

    function setCameraVisible(visible) {
        if (cameraStream) {
            cameraStream.hidden = !visible;
            cameraStream.style.display = visible ? 'block' : 'none';
        }
        if (cameraPlaceholder) {
            cameraPlaceholder.style.display = visible ? 'none' : 'flex';
        }
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
        if (timerElement) {
            timerElement.textContent = formatTime(elapsedSeconds);
        }
    }

    function setButtonState(running) {
        startButton.disabled = running;
        stopButton.disabled = !running;
        generateButton.disabled = running || elapsedSeconds === 0;
        downloadButton.disabled = running || elapsedSeconds === 0;
    }

    function syncWarningCardState() {
        const card = warningCountElement && warningCountElement.closest('.warning-card');
        if (!card || !warningCountElement) {
            return;
        }

        const count = Number.parseInt(warningCountElement.textContent, 10) || 0;
        card.classList.toggle('warning-active', count > 0);
    }

    function setAiStatus(statusText) {
        if (!aiStatusBadge) {
            return;
        }

        aiStatusBadge.textContent = statusText;
        aiStatusBadge.classList.remove('status-neutral', 'status-success', 'status-alert', 'status-warning');

        if (statusText === 'Fully Operational') {
            aiStatusBadge.classList.add('status-success');
        } else if (
            statusText.includes('Not Detected') ||
            statusText.includes('Looking Away') ||
            statusText.includes('Phone Detected') ||
            statusText.includes('Multiple')
        ) {
            aiStatusBadge.classList.add('status-alert');
        } else if (statusText.includes('Fallback') || statusText.includes('Initializing')) {
            aiStatusBadge.classList.add('status-warning');
        } else {
            aiStatusBadge.classList.add('status-neutral');
        }
    }

    function setCameraStatus(statusText) {
        if (!cameraStatusBadge) {
            return;
        }
        cameraStatusBadge.textContent = statusText;
        cameraStatusBadge.classList.remove('status-neutral', 'status-success', 'status-alert');
        if (statusText.includes('Ready')) {
            cameraStatusBadge.classList.add('status-success');
        } else if (statusText.includes('Error') || statusText.includes('Not Found')) {
            cameraStatusBadge.classList.add('status-alert');
        } else {
            cameraStatusBadge.classList.add('status-neutral');
        }
    }

    function updateCheckItem(element, valueElement, isNormal, normalText, alertText) {
        if (!element || !valueElement) {
            return;
        }
        const iconSpan = element.querySelector('.check-icon');
        if (isNormal) {
            element.classList.remove('check-alert');
            element.classList.add('check-normal');
            if (iconSpan) iconSpan.textContent = '✓';
            valueElement.textContent = normalText;
        } else {
            element.classList.remove('check-normal');
            element.classList.add('check-alert');
            if (iconSpan) iconSpan.textContent = '⚠';
            valueElement.textContent = alertText;
        }
    }

    function updatePoseCheck(data) {
        const checks = data.live_checks || {};
        const gazeState = data.gaze_state || 'DETECTION_UNAVAILABLE';
        if (gazeState === 'CALIBRATING') {
            updateCheckItem(checkPose, valPose, true, 'Calibrating...', 'Calibrating...');
            if (valPose) valPose.textContent = 'Calibrating...';
        } else if (gazeState === 'CALIBRATION_FAILED' || gazeState === 'DETECTION_UNAVAILABLE') {
            updateCheckItem(checkPose, valPose, false, 'Detection unavailable', 'Detection unavailable');
        } else if (gazeState === 'NORMAL') {
            updateCheckItem(checkPose, valPose, true, 'Looking at screen', 'Looking at screen');
        } else {
            const dir = (checks.gaze_direction || data.gaze_direction || 'LOOKING_AT_SCREEN').toUpperCase();
            const isNormal = dir === 'NORMAL' || dir === 'LOOKING_AT_SCREEN';
            const dirLabel = isNormal ? 'Looking at screen' : `Looking away (${dir.replace('LOOKING_', '').replace('_', ' ')})`;
            updateCheckItem(checkPose, valPose, isNormal, 'Looking at screen', dirLabel);
        }
    }

    function updateAlertBanner(activeAlerts) {
        if (!liveAlertBanner || !alertMessage) {
            return;
        }

        if (examRunning && activeAlerts && activeAlerts.length > 0) {
            alertMessage.textContent = activeAlerts.join(' • ');
            liveAlertBanner.classList.remove('alert-hidden');
            liveAlertBanner.classList.add('alert-visible');
        } else {
            liveAlertBanner.classList.remove('alert-visible');
            liveAlertBanner.classList.add('alert-hidden');
        }
    }

    async function pollAiStatus() {
        try {
            const response = await fetch('/exam/status', { cache: 'no-store' });
            if (!response.ok) {
                throw new Error('status request failed');
            }

            const data = await response.json();

            setAiStatus(data.ai_status || 'Monitoring');
            setCameraStatus(data.camera_status || 'Camera Off');

            if (data.running && examStateMessage) {
                if (data.calibration_status === 'pending') {
                    examStateMessage.textContent = 'CALIBRATING - Please look normally at the screen...';
                } else if (data.calibration_status === 'failed') {
                    examStateMessage.textContent = 'CALIBRATION FAILED - Please position your face clearly in front of the camera.';
                } else if (data.calibration_status === 'complete' && examStateMessage.textContent.startsWith('CALIBRATING')) {
                    examStateMessage.textContent = 'CALIBRATION COMPLETE - Monitoring active.';
                }
            }

            if (warningCountElement && Number.isFinite(data.warning_count)) {
                warningCountElement.textContent = data.warning_count;
                syncWarningCardState();
                if (warningDetailMessage) {
                    if (data.warning_count > 0) {
                        warningDetailMessage.textContent = `${data.warning_count} irregularity incident(s) recorded for this session.`;
                    } else {
                        warningDetailMessage.textContent = 'No exam irregularities recorded.';
                    }
                }
            }

            // Update live proctoring checklist
            if (data.running) {
                const checks = data.live_checks || {};
                const faceNormal = checks.face_detected !== false;
                updateCheckItem(
                    checkFace,
                    valFace,
                    faceNormal,
                    'Face Present (Verified)',
                    'Face Missing / Obscured'
                );

                updatePoseCheck(data);

                const personNormal = checks.single_person !== false;
                const pCount = checks.person_count || 1;
                updateCheckItem(
                    checkPerson,
                    valPerson,
                    personNormal,
                    'Single Candidate (1)',
                    `Multiple People (${pCount})`
                );

                const phoneNormal = checks.no_phone !== false;
                updateCheckItem(
                    checkPhone,
                    valPhone,
                    phoneNormal,
                    'No Devices Detected',
                    'Mobile Phone Detected'
                );

                updateAlertBanner(data.active_alerts || []);
            } else {
                updateAlertBanner([]);
            }

            // Update Subsystem Diagnostics
            if (data.subsystems) {
                if (sysCamera) sysCamera.textContent = data.subsystems.camera || 'Ready';
                if (sysFace) sysFace.textContent = data.subsystems.face_detector || 'MediaPipe Tasks';
                if (sysPose) sysPose.textContent = data.subsystems.head_pose || 'MediaPipe Tasks';
                if (sysYolo) sysYolo.textContent = data.subsystems.person_detector || 'YOLOv8n';
            }
        } catch (error) {
            setAiStatus('Monitoring');
            setCameraStatus('Camera Off');
            updateAlertBanner([]);
        }
    }

    function startTimer() {
        if (examRunning) {
            return;
        }

        examRunning = true;
        examStateMessage.textContent = 'Proctoring is ACTIVE. Video and behavior are being monitored.';
        setButtonState(true);

        if (cameraStream) {
            setCameraVisible(true);
            // Append timestamp to prevent browser image caching
            cameraStream.src = `/video_feed?t=${Date.now()}`;
        }

        timerInterval = window.setInterval(() => {
            elapsedSeconds += 1;
            renderTimer();
        }, 1000);
    }

    async function stopTimer() {
        if (!examRunning) {
            return;
        }

        examRunning = false;
        if (timerInterval !== null) {
            window.clearInterval(timerInterval);
            timerInterval = null;
        }

        const stopRequest = fetch('/exam/stop', { method: 'POST', keepalive: true })
            .then((response) => response.json())
            .then((data) => {
                lastSessionId = data.session_id || lastSessionId;
                if (data.risk_category) {
                    examStateMessage.textContent = `Exam ended. Session Risk: ${data.risk_score} (${data.risk_category}). Reports are available.`;
                } else {
                    examStateMessage.textContent = 'Exam stopped. Reports are now available.';
                }
            })
            .catch(() => {
                examStateMessage.textContent = 'Exam stopped. Reports are now available.';
            });

        if (cameraStream) {
            setCameraVisible(false);
            cameraStream.removeAttribute('src');
        }

        updateAlertBanner([]);
        setButtonState(false);
        await stopRequest;
        pollAiStatus();
    }

    function resetReportButtons() {
        generateButton.disabled = true;
        downloadButton.disabled = true;
    }

    startButton.addEventListener('click', async () => {
        if (!examRunning) {
            startButton.disabled = true;
            examStateMessage.textContent = 'Starting camera & initializing exam session...';

            try {
                const response = await fetch('/exam/start', { method: 'POST' });
                const data = await response.json();
                if (!response.ok || !data.running) {
                    throw new Error(data.error || 'Exam could not be started.');
                }

                renderTimer();
                resetReportButtons();
                lastSessionId = data.session_id || null;
                setCameraStatus(data.camera_status || 'Camera Ready');
                setAiStatus(data.ai_status || 'Monitoring');
                startTimer();
                pollAiStatus();
            } catch (error) {
                startButton.disabled = false;
                examStateMessage.textContent = error.message || 'Exam could not be started.';
            }
        }
    });

    stopButton.addEventListener('click', async () => {
        await stopTimer();
    });

    generateButton.addEventListener('click', () => {
        if (!generateButton.disabled && lastSessionId !== null) {
            generateButton.disabled = true;
            examStateMessage.textContent = 'Generating PDF report...';
            fetch(`/report/${lastSessionId}/generate`)
                .then((response) => {
                    if (!response.ok) {
                        throw new Error('Report generation failed.');
                    }
                    return response.json();
                })
                .then(() => {
                    examStateMessage.textContent = 'Report generated successfully. Ready to download.';
                    downloadButton.disabled = false;
                    generateButton.disabled = false;
                })
                .catch((error) => {
                    examStateMessage.textContent = error.message;
                    generateButton.disabled = false;
                });
        }
    });

    downloadButton.addEventListener('click', () => {
        if (!downloadButton.disabled && lastSessionId !== null) {
            window.location.href = `/report/${lastSessionId}/download`;
        }
    });

    renderTimer();
    setButtonState(false);
    setCameraVisible(false);
    syncWarningCardState();
    setAiStatus(aiStatusBadge ? aiStatusBadge.textContent.trim() : 'Monitoring');
    pollAiStatus();
    statusInterval = window.setInterval(pollAiStatus, 1000);

    window.addEventListener('beforeunload', () => {
        if (statusInterval !== null) {
            window.clearInterval(statusInterval);
        }
    });
});
