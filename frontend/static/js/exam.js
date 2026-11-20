document.addEventListener("DOMContentLoaded", () => {
    const questions = window.examData.questions || [];
    const answers = new Map();
    const reviewed = new Set();
    let current = 0;
    const serverRemaining = window.examData.remaining_seconds != null ? Number(window.examData.remaining_seconds) : null;
    let remainingSeconds = serverRemaining !== null ? Math.max(serverRemaining, 0) : Math.max(Number(window.examData.duration_minutes || 0) * 60, 0);

    function escapeHtml(text) {
        if (text == null) return "";
        return String(text)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    // Pre-populate previously saved answers
    questions.forEach((q) => {
        const qtype = (q.question_type || "mcq").toLowerCase();
        if (qtype === "multiple_select") {
            const set = new Set();
            if (q.answer_text) {
                q.answer_text.split(",").forEach(s => {
                    const n = Number(s.trim());
                    if (!isNaN(n) && n > 0) set.add(n);
                });
            }
            if (set.size === 0 && q.option_id != null && q.option_id !== "") {
                set.add(Number(q.option_id));
            }
            if (set.size > 0) answers.set(q.question_id, set);
        } else if (qtype === "short_answer" || qtype === "long_answer" || qtype === "fill_in_the_blanks" || qtype === "fill_blank" || qtype === "numerical") {
            if (q.answer_text != null && q.answer_text !== "") {
                answers.set(q.question_id, String(q.answer_text));
            }
        } else {
            if (q.option_id != null && q.option_id !== "") {
                answers.set(q.question_id, Number(q.option_id));
            }
        }
    });
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

        const qtype = (question.question_type || "mcq").toLowerCase();
        let inputHtml = "";

        if (qtype === "multiple_select") {
            const selectedSet = answers.get(question.question_id) instanceof Set ? answers.get(question.question_id) : new Set();
            inputHtml = `<div class="option-list" style="margin-top: 1rem;">
                <p style="font-size: 0.8rem; color: #4b5563; font-weight: 600; margin-bottom: 8px;">Select all that apply:</p>
                ${(question.options || []).map((opt) => `
                <label class="option-row" style="display: flex; align-items: center; gap: 10px; padding: 10px 14px; border: 1px solid #e2e8f0; border-radius: 8px; margin-bottom: 8px; cursor: pointer;">
                    <input type="checkbox" name="msq_${question.question_id}" value="${opt.option_id}" ${selectedSet.has(opt.option_id) ? "checked" : ""}>
                    <span>${escapeHtml(opt.option_text)}</span>
                </label>`).join("")}
            </div>`;
        } else if (qtype === "short_answer" || qtype === "long_answer") {
            const val = typeof answers.get(question.question_id) === "string" ? answers.get(question.question_id) : "";
            const rows = qtype === "long_answer" ? 6 : 3;
            inputHtml = `<div style="margin: 1.5rem 0;">
                <textarea rows="${rows}" class="text-answer-field" placeholder="Type your response here..." style="width: 100%; box-sizing: border-box; padding: 12px 14px; font-size: 0.95rem; border-radius: 8px; border: 1px solid #cbd5e1; font-family: inherit;">${escapeHtml(val)}</textarea>
            </div>`;
        } else if (qtype === "numerical") {
            const val = typeof answers.get(question.question_id) === "string" ? answers.get(question.question_id) : "";
            inputHtml = `<div style="margin: 1.5rem 0;">
                <label style="display: block; font-size: 0.85rem; color: #4b5563; margin-bottom: 6px; font-weight: 600;">Numerical Answer:</label>
                <input type="number" step="any" class="text-answer-field" placeholder="e.g. 42.5" value="${escapeHtml(val)}" style="width: 100%; max-width: 320px; box-sizing: border-box; padding: 10px 14px; font-size: 1rem; border-radius: 8px; border: 1px solid #cbd5e1;">
            </div>`;
        } else if (qtype === "fill_in_the_blanks" || qtype === "fill_blank") {
            const val = typeof answers.get(question.question_id) === "string" ? answers.get(question.question_id) : "";
            inputHtml = `<div style="margin: 1.5rem 0;">
                <label style="display: block; font-size: 0.85rem; color: #4b5563; margin-bottom: 6px; font-weight: 600;">Your Answer:</label>
                <input type="text" class="text-answer-field" placeholder="Type answer here..." value="${escapeHtml(val)}" style="width: 100%; max-width: 480px; box-sizing: border-box; padding: 10px 14px; font-size: 1rem; border-radius: 8px; border: 1px solid #cbd5e1;">
            </div>`;
        } else {
            // MCQ or True/False
            const selectedOpt = answers.get(question.question_id);
            inputHtml = `<div class="option-list" style="margin-top: 1rem;">
                ${(question.options || []).map((opt) => `
                <label class="option-row" style="display: flex; align-items: center; gap: 10px; padding: 10px 14px; border: 1px solid #e2e8f0; border-radius: 8px; margin-bottom: 8px; cursor: pointer;">
                    <input type="radio" name="answer_${question.question_id}" value="${opt.option_id}" ${selectedOpt === opt.option_id ? "checked" : ""}>
                    <span>${escapeHtml(opt.option_text)}</span>
                </label>`).join("")}
            </div>`;
        }

        mount.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <p class="eyebrow" style="margin: 0;">Question ${current + 1} of ${questions.length}</p>
                <span class="badge" style="font-size: 0.75rem; background: #eff6ff; color: #1e40af; border: 1px solid #bfdbfe; padding: 2px 8px; border-radius: 4px;">
                    ${question.marks || 1} mark${question.marks === 1 ? '' : 's'} &bull; ${(qtype || 'MCQ').toUpperCase().replace('_', ' ')}
                </span>
            </div>
            <h1 style="font-size: 1.25rem; line-height: 1.4; margin-top: 4px;">${escapeHtml(question.statement)}</h1>
            ${question.image_url ? `<div style="margin: 12px 0;"><img src="${question.image_url}" style="max-width: 100%; max-height: 300px; border-radius: 6px; border: 1px solid #e2e8f0;"></div>` : ''}
            ${inputHtml}
        `;

        // Wire inputs
        if (qtype === "multiple_select") {
            mount.querySelectorAll("input[type=checkbox]").forEach((chk) => {
                chk.addEventListener("change", () => {
                    let set = answers.get(question.question_id);
                    if (!(set instanceof Set)) {
                        set = new Set();
                        answers.set(question.question_id, set);
                    }
                    const val = Number(chk.value);
                    if (chk.checked) set.add(val);
                    else set.delete(val);

                    if (set.size === 0) answers.delete(question.question_id);
                    save(question.question_id, {
                        option_ids: Array.from(set),
                        answer_text: Array.from(set).join(",")
                    });
                    renderPalette();
                });
            });
        } else if (qtype === "short_answer" || qtype === "long_answer" || qtype === "numerical" || qtype === "fill_in_the_blanks" || qtype === "fill_blank") {
            const field = mount.querySelector(".text-answer-field");
            if (field) {
                let debounceTimer = null;
                field.addEventListener("input", () => {
                    const text = field.value.trim();
                    if (text) answers.set(question.question_id, field.value);
                    else answers.delete(question.question_id);
                    renderPalette();

                    clearTimeout(debounceTimer);
                    debounceTimer = setTimeout(() => {
                        save(question.question_id, { answer_text: field.value });
                    }, 600);
                });
                field.addEventListener("blur", () => {
                    save(question.question_id, { answer_text: field.value });
                });
            }
        } else {
            // Radio inputs
            mount.querySelectorAll("input[type=radio]").forEach((input) => input.addEventListener("change", () => {
                const optId = Number(input.value);
                answers.set(question.question_id, optId);
                save(question.question_id, { option_id: optId });
                renderPalette();
            }));
        }

        document.getElementById("previousQuestion").disabled = current === 0;
        document.getElementById("nextQuestion").textContent = current === questions.length - 1 ? "Save answer" : "Save & next";
        document.getElementById("markReview").textContent = reviewed.has(question.question_id) ? "Unmark review" : "Mark for review";
    }

    async function save(questionId, payload) {
        saveStatus.textContent = "Saving...";
        try {
            const body = { question_id: questionId };
            if (typeof payload === "number") {
                body.option_id = payload;
            } else if (typeof payload === "object") {
                Object.assign(body, payload);
            }
            const response = await fetch(`/api/attempt/${window.attemptId}/answer`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(body)
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
