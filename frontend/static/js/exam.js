document.addEventListener("DOMContentLoaded", () => {
    const questions = window.examData.questions || [];
    const answers = new Map();
    const reviewed = new Set();
    let current = 0;
    let remainingSeconds = Math.max(Number(window.examData.duration_minutes || 0) * 60, 0);
    let timerId = null;
    let statusId = null;
    let cameraStream = null;
    let frameCanvas = null;
    let frameTimer = null;
    let examStopped = false;

    const mount = document.getElementById("questionMount");
    const palette = document.getElementById("questionPalette");
    const saveStatus = document.getElementById("saveStatus");
    const video = document.getElementById("cameraVideo");
    const cameraMessage = document.getElementById("cameraMessage");
    const cameraError = document.getElementById("cameraError");
    const cameraStatus = document.getElementById("cameraStatus");
    const timer = document.getElementById("examTimer");
    const warningCount = document.getElementById("warningCount");
    const recentWarnings = document.getElementById("recentWarnings");
    const faceStatus = document.getElementById("faceStatus");
    const aiStatus = document.getElementById("aiStatus");

    function setStatus(element, text, state) {
        element.textContent = text;
        element.classList.remove("status-neutral", "status-success", "status-warning", "status-alert");
        element.classList.add(`status-${state}`);
    }

    function renderTimer() {
        const minutes = Math.floor(remainingSeconds / 60);
        const seconds = remainingSeconds % 60;
        timer.textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    }

    function render() {
        const question = questions[current];
        if (!question) {
            mount.innerHTML = "<p class='empty-state'>No questions are available.</p>";
            return;
        }
        mount.innerHTML = `<p class="eyebrow">Question ${current + 1} of ${questions.length}</p>
            <h1>${question.statement}</h1>
            <div class="option-list">${question.options.map((option) => `
            <label class="option-row"><input type="radio" name="answer" value="${option.option_id}" ${answers.get(question.question_id) === option.option_id ? "checked" : ""}><span>${option.option_text}</span></label>`).join("")}</div>`;
        mount.querySelectorAll("input").forEach((input) => input.addEventListener("change", () => {
            answers.set(question.question_id, Number(input.value));
            save(question.question_id, Number(input.value));
            renderPalette();
        }));
        document.getElementById("previousQuestion").disabled = current === 0;
        document.getElementById("nextQuestion").textContent = current === questions.length - 1 ? "Save answer" : "Save & next";
        document.getElementById("markReview").textContent = reviewed.has(question.question_id) ? "Unmark review" : "Mark for review";
    }

    async function save(questionId, optionId) {
        saveStatus.textContent = "Saving...";
        try {
            const response = await fetch(`/api/attempt/${window.attemptId}/answer`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({question_id: questionId, option_id: optionId})
            });
            if (!response.ok) throw new Error("Answer could not be saved.");
            saveStatus.textContent = "Saved";
        } catch (error) {
            saveStatus.textContent = error.message;
        }
    }

    function renderPalette() {
        palette.innerHTML = questions.map((question, index) =>
            `<button type="button" class="palette-button ${answers.has(question.question_id) ? "answered" : ""} ${reviewed.has(question.question_id) ? "reviewed" : ""} ${index === current ? "current" : ""}">${index + 1}</button>`
        ).join("");
        palette.querySelectorAll("button").forEach((button, index) => button.addEventListener("click", () => {
            current = index;
            render();
            renderPalette();
        }));
    }

    function updateProctorStatus(data) {
        if (data.running) {
            setStatus(cameraStatus, data.camera_status || "Camera ready", "success");
        } else if (data.error || data.camera_status === "Camera Off") {
            setStatus(cameraStatus, data.camera_status || "Camera unavailable", "alert");
        }
        const faceDetected = data.live_checks && data.live_checks.face_detected;
        faceStatus.textContent = faceDetected ? "Detected" : (data.running ? "Not detected" : "Waiting");
        document.getElementById("faceCheck").classList.toggle("check-normal", Boolean(faceDetected));
        document.getElementById("faceCheck").classList.toggle("check-alert", !faceDetected);
        if (data.running && data.ai_ready) {
            aiStatus.textContent = data.ai_status || "Running";
            document.getElementById("aiCheck").classList.toggle("check-normal", true);
            document.getElementById("aiCheck").classList.toggle("check-alert", false);
        } else {
            aiStatus.textContent = data.error || "Not running";
            document.getElementById("aiCheck").classList.toggle("check-normal", false);
            document.getElementById("aiCheck").classList.toggle("check-alert", true);
        }
        warningCount.textContent = String(data.warning_count || 0);
        const warnings = data.recent_warnings || data.active_alerts || [];
        recentWarnings.innerHTML = warnings.length
            ? warnings.map((warning) => `<li>${warning}</li>`).join("")
            : "<li>No active warnings.</li>";
    }

    async function pollProctorStatus() {
        try {
            const response = await fetch("/exam/status", {cache: "no-store"});
            if (!response.ok) throw new Error("Unable to read proctoring status.");
            updateProctorStatus(await response.json());
        } catch (error) {
            setStatus(cameraStatus, "Status unavailable", "alert");
            aiStatus.textContent = error.message;
        }
    }

    async function startProctoring() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            throw new Error("This browser does not support camera access. Use a current browser over HTTPS or localhost.");
        }
        cameraStream = await navigator.mediaDevices.getUserMedia({video: {width: {ideal: 1280}, height: {ideal: 720}}, audio: false});
        video.srcObject = cameraStream;
        cameraMessage.hidden = true;
        video.hidden = false;
        setStatus(cameraStatus, "Camera connected", "success");

        const startResponse = await fetch("/exam/start", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({source: "browser"})});
        const data = await startResponse.json();
        if (!startResponse.ok || !data.running) {
            stopCamera();
            throw new Error(data.error || "The AI proctoring engine could not start.");
        }
        updateProctorStatus(data);
        statusId = window.setInterval(pollProctorStatus, 1500);
        frameCanvas = document.createElement("canvas");
        frameCanvas.width = 640;
        frameCanvas.height = 480;
        frameTimer = window.setInterval(sendFrameToProctor, 250);
        timerId = window.setInterval(() => {
            remainingSeconds -= 1;
            renderTimer();
            if (remainingSeconds <= 0) {
                window.clearInterval(timerId);
                finishExam();
            }
        }, 1000);
    }

    function stopCamera() {
        if (cameraStream) {
            cameraStream.getTracks().forEach((track) => track.stop());
            cameraStream = null;
        }
        video.srcObject = null;
    }

    async function stopProctoring() {
        if (examStopped) return;
        examStopped = true;
        if (statusId) window.clearInterval(statusId);
        if (frameTimer) window.clearInterval(frameTimer);
        stopCamera();
        try {
            await fetch("/exam/stop", {method: "POST", keepalive: true});
        } catch (error) {
            setStatus(cameraStatus, "Stop failed", "alert");
        }

    }

    async function sendFrameToProctor() {
        if (!cameraStream || video.readyState < 2 || !frameCanvas) return;
        const context = frameCanvas.getContext("2d");
        context.drawImage(video, 0, 0, frameCanvas.width, frameCanvas.height);
        const blob = await new Promise((resolve) => frameCanvas.toBlob(resolve, "image/jpeg", 0.75));
        if (blob) await fetch("/exam/frame", {method: "POST", headers: {"Content-Type": "image/jpeg"}, body: blob});
    }

    function finishExam() {
        stopProctoring().finally(() => {
            document.getElementById("submitForm").submit();
        });
    }

    document.getElementById("previousQuestion").addEventListener("click", () => {
        if (current > 0) { current -= 1; render(); renderPalette(); }
    });
    document.getElementById("nextQuestion").addEventListener("click", () => {
        if (current < questions.length - 1) { current += 1; render(); renderPalette(); }
    });
    document.getElementById("markReview").addEventListener("click", () => {
        const questionId = questions[current].question_id;
        if (reviewed.has(questionId)) reviewed.delete(questionId);
        else reviewed.add(questionId);
        render();
        renderPalette();
    });
    document.getElementById("submitForm").addEventListener("submit", (event) => {
        if (examStopped) return;
        event.preventDefault();
        if (window.confirm("Submit this examination? You will not be able to change your answers.")) finishExam();
    });
    window.addEventListener("pagehide", () => {
        if (!examStopped) {
            stopCamera();
            navigator.sendBeacon("/exam/stop", new Blob([], {type: "application/json"}));
        }
    });

    render();
    renderPalette();
    renderTimer();
    startProctoring().catch((error) => {
        cameraMessage.hidden = false;
        video.hidden = true;
        cameraError.hidden = false;
        cameraError.textContent = error.message;
        setStatus(cameraStatus, "Camera unavailable", "alert");
        aiStatus.textContent = "Not running";
    });
});
