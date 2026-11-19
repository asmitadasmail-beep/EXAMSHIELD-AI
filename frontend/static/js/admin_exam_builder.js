/**
 * EXAMSHIELD AI - Question Paper Builder
 * Full interactive question paper creation, bank import, drag-and-drop reordering,
 * live summary calculation, and official paper preview.
 */

(function () {
    'use strict';

    // State
    let questions = [];
    let bankCache = [];
    let previewAnswerKeyVisible = false;

    // DOM Elements Cache
    let elements = {};

    document.addEventListener('DOMContentLoaded', init);

    function init() {
        cacheDOMElements();
        bindEvents();
        // If empty, add a default starter MCQ question
        if (questions.length === 0) {
            addQuestion('mcq', false);
        }
        updateSummary();
    }

    function cacheDOMElements() {
        elements = {
            form: document.getElementById('exam-details-form'),
            titleInput: document.getElementById('exam-title'),
            subjectSelect: document.getElementById('exam-subject'),
            subjectCustom: document.getElementById('exam-subject-custom'),
            deptInput: document.getElementById('exam-department'),
            semSelect: document.getElementById('exam-semester'),
            yearInput: document.getElementById('exam-academic-year'),
            descInput: document.getElementById('exam-description'),
            durationInput: document.getElementById('exam-duration'),
            totalMarksInput: document.getElementById('exam-total-marks'),
            passMarksInput: document.getElementById('exam-pass-marks'),
            negativeMarkInput: document.getElementById('exam-negative-marking'),
            startTimeInput: document.getElementById('exam-start-time'),
            endTimeInput: document.getElementById('exam-end-time'),
            randQCheck: document.getElementById('exam-rand-questions'),
            randOptCheck: document.getElementById('exam-rand-options'),
            multiAttemptCheck: document.getElementById('exam-multi-attempts'),
            instructionsInput: document.getElementById('exam-instructions'),
            csrfToken: document.querySelector('input[name="csrf_token"]')?.value || '',

            // Summary bar elements
            statQCount: document.getElementById('stat-q-count'),
            statTotalMarks: document.getElementById('stat-total-marks'),
            statDuration: document.getElementById('stat-duration'),
            statAvgMarks: document.getElementById('stat-avg-marks'),

            // Question container
            questionsContainer: document.getElementById('questions-container'),
            emptyState: document.getElementById('questions-empty-state'),

            // Modals
            previewModal: document.getElementById('preview-modal'),
            previewContent: document.getElementById('preview-modal-content'),
            bankModal: document.getElementById('bank-modal'),
            bankQuestionsList: document.getElementById('bank-questions-list'),
            bankSearchInput: document.getElementById('bank-search-input'),
            bankSubjectFilter: document.getElementById('bank-subject-filter'),

            // Notifications
            alertBanner: document.getElementById('builder-alert'),
        };
    }

    function bindEvents() {
        // Duration change
        if (elements.durationInput) {
            elements.durationInput.addEventListener('input', updateSummary);
        }

        // Custom subject toggle
        if (elements.subjectSelect) {
            elements.subjectSelect.addEventListener('change', function () {
                if (this.value === '__custom__') {
                    elements.subjectCustom.style.display = 'block';
                    elements.subjectCustom.focus();
                } else {
                    elements.subjectCustom.style.display = 'none';
                }
            });
        }

        // Save actions
        document.querySelectorAll('[data-action="save-draft"]').forEach(btn => {
            btn.addEventListener('click', () => saveExam('draft'));
        });
        document.querySelectorAll('[data-action="publish-exam"]').forEach(btn => {
            btn.addEventListener('click', () => saveExam('published'));
        });
        document.querySelectorAll('[data-action="schedule-exam"]').forEach(btn => {
            btn.addEventListener('click', () => saveExam('scheduled'));
        });

        // Preview modal
        document.querySelectorAll('[data-action="open-preview"]').forEach(btn => {
            btn.addEventListener('click', openPreviewModal);
        });
        document.querySelectorAll('[data-action="close-preview"]').forEach(btn => {
            btn.addEventListener('click', closePreviewModal);
        });
        const toggleKeyBtn = document.getElementById('btn-toggle-modal-answer-key');
        if (toggleKeyBtn) {
            toggleKeyBtn.addEventListener('click', togglePreviewModalAnswerKey);
        }
        const printBtn = document.getElementById('btn-print-modal-paper');
        if (printBtn) {
            printBtn.addEventListener('click', () => window.print());
        }

        // Question Bank modal
        document.querySelectorAll('[data-action="open-bank"]').forEach(btn => {
            btn.addEventListener('click', openBankModal);
        });
        document.querySelectorAll('[data-action="close-bank"]').forEach(btn => {
            btn.addEventListener('click', closeBankModal);
        });
        if (elements.bankSearchInput) {
            elements.bankSearchInput.addEventListener('input', renderBankList);
        }
        if (elements.bankSubjectFilter) {
            elements.bankSubjectFilter.addEventListener('change', renderBankList);
        }

        // Add question buttons
        document.querySelectorAll('[data-add-type]').forEach(btn => {
            btn.addEventListener('click', function () {
                const type = this.getAttribute('data-add-type') || 'mcq';
                addQuestion(type, true);
            });
        });
    }

    // ==========================================
    // Question Model & Factory
    // ==========================================
    function createDefaultQuestion(type) {
        const base = {
            id: 'q_' + Date.now() + '_' + Math.random().toString(36).substring(2, 7),
            statement: '',
            question_type: type,
            marks: type === 'long_answer' ? 5 : (type === 'short_answer' ? 2 : 1),
            difficulty: 'Medium',
            topic: '',
            image_url: '',
            explanation: '',
            negative_marks: 0,
            correct_answer_text: '',
            options: [],
        };

        if (type === 'mcq' || type === 'multiple_select') {
            base.options = [
                { option_text: 'Option 1', is_correct: true },
                { option_text: 'Option 2', is_correct: false },
                { option_text: 'Option 3', is_correct: false },
                { option_text: 'Option 4', is_correct: false },
            ];
        } else if (type === 'true_false') {
            base.options = [
                { option_text: 'True', is_correct: true },
                { option_text: 'False', is_correct: false },
            ];
        }

        return base;
    }

    function addQuestion(type, scrollIntoView = true) {
        const q = createDefaultQuestion(type);
        questions.push(q);
        renderQuestions();
        updateSummary();

        if (scrollIntoView) {
            setTimeout(() => {
                const card = document.getElementById(q.id);
                if (card) {
                    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    const textarea = card.querySelector('.q-statement-input');
                    if (textarea) textarea.focus();
                }
            }, 50);
        }
    }

    function deleteQuestion(index) {
        if (questions.length <= 1) {
            showAlert('The examination paper must contain at least one question.', 'warning');
            return;
        }
        questions.splice(index, 1);
        renderQuestions();
        updateSummary();
    }

    function duplicateQuestion(index) {
        const source = questions[index];
        const clone = JSON.parse(JSON.stringify(source));
        clone.id = 'q_' + Date.now() + '_' + Math.random().toString(36).substring(2, 7);
        clone.statement = source.statement ? `${source.statement} (Copy)` : '';
        questions.splice(index + 1, 0, clone);
        renderQuestions();
        updateSummary();
    }

    function moveQuestion(index, direction) {
        const targetIndex = index + direction;
        if (targetIndex < 0 || targetIndex >= questions.length) return;
        const item = questions.splice(index, 1)[0];
        questions.splice(targetIndex, 0, item);
        renderQuestions();
    }

    function changeQuestionType(index, newType) {
        const q = questions[index];
        if (q.question_type === newType) return;
        q.question_type = newType;

        if (newType === 'mcq' || newType === 'multiple_select') {
            if (!q.options || q.options.length < 2) {
                q.options = [
                    { option_text: 'Option 1', is_correct: true },
                    { option_text: 'Option 2', is_correct: false },
                    { option_text: 'Option 3', is_correct: false },
                    { option_text: 'Option 4', is_correct: false },
                ];
            }
        } else if (newType === 'true_false') {
            q.options = [
                { option_text: 'True', is_correct: true },
                { option_text: 'False', is_correct: false },
            ];
        }
        renderQuestions();
    }

    // ==========================================
    // Summary & Calculations
    // ==========================================
    function updateSummary() {
        const count = questions.length;
        let totalMarks = 0;
        questions.forEach(q => {
            const m = parseFloat(q.marks) || 0;
            totalMarks += m;
        });

        // Format marks cleanly
        const formattedTotal = Number.isInteger(totalMarks) ? totalMarks : totalMarks.toFixed(2);
        const avg = count > 0 ? (totalMarks / count).toFixed(1) : 0;
        const duration = elements.durationInput ? elements.durationInput.value : 45;

        if (elements.statQCount) elements.statQCount.textContent = count;
        if (elements.statTotalMarks) elements.statTotalMarks.textContent = formattedTotal;
        if (elements.statDuration) elements.statDuration.textContent = `${duration} min`;
        if (elements.statAvgMarks) elements.statAvgMarks.textContent = avg;

        if (elements.totalMarksInput) {
            elements.totalMarksInput.value = formattedTotal;
        }

        // Show / hide empty state
        if (elements.emptyState) {
            elements.emptyState.style.display = count === 0 ? 'block' : 'none';
        }
    }

    // ==========================================
    // Render Question Cards
    // ==========================================
    function renderQuestions() {
        if (!elements.questionsContainer) return;
        elements.questionsContainer.innerHTML = '';

        questions.forEach((q, index) => {
            const card = document.createElement('div');
            card.className = 'builder-question-card';
            card.id = q.id;
            card.setAttribute('draggable', 'true');
            card.dataset.index = index;

            // Card Header
            const header = document.createElement('div');
            header.className = 'question-card-header';
            header.innerHTML = `
                <div class="question-header-left">
                    <span class="drag-handle" title="Drag to reorder">&#x2630;</span>
                    <span class="question-num-tag">Question ${index + 1}</span>
                    <span class="question-type-badge">${getTypeLabel(q.question_type)}</span>
                </div>
                <div class="question-header-right">
                    <button type="button" class="btn-icon-subtle btn-move-up" title="Move Up" ${index === 0 ? 'disabled' : ''}>&uarr;</button>
                    <button type="button" class="btn-icon-subtle btn-move-down" title="Move Down" ${index === questions.length - 1 ? 'disabled' : ''}>&darr;</button>
                    <button type="button" class="btn-icon-subtle btn-duplicate" title="Duplicate Question">&#x2398;</button>
                    <button type="button" class="btn-icon-subtle btn-delete" title="Delete Question">&times;</button>
                </div>
            `;

            // Card Body
            const body = document.createElement('div');
            body.className = 'question-card-body';

            // Top meta row: Type, Marks, Difficulty, Topic, Negative Marks
            const metaGrid = document.createElement('div');
            metaGrid.className = 'admin-form-grid';
            metaGrid.style.gridTemplateColumns = 'repeat(auto-fit, minmax(140px, 1fr))';
            metaGrid.style.gap = '10px';
            metaGrid.innerHTML = `
                <div class="form-group">
                    <label>Question Type</label>
                    <select class="form-control q-type-select">
                        <option value="mcq" ${q.question_type === 'mcq' ? 'selected' : ''}>Multiple Choice (MCQ)</option>
                        <option value="multiple_select" ${q.question_type === 'multiple_select' ? 'selected' : ''}>Multiple Select (MSQ)</option>
                        <option value="true_false" ${q.question_type === 'true_false' ? 'selected' : ''}>True / False</option>
                        <option value="fill_blank" ${q.question_type === 'fill_blank' ? 'selected' : ''}>Fill in the Blanks</option>
                        <option value="short_answer" ${q.question_type === 'short_answer' ? 'selected' : ''}>Short Answer</option>
                        <option value="long_answer" ${q.question_type === 'long_answer' ? 'selected' : ''}>Long Answer</option>
                        <option value="numerical" ${q.question_type === 'numerical' ? 'selected' : ''}>Numerical Question</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Marks</label>
                    <input type="number" class="form-control q-marks-input" value="${q.marks}" min="0.5" step="0.5">
                </div>
                <div class="form-group">
                    <label>Difficulty</label>
                    <select class="form-control q-diff-select">
                        <option value="Easy" ${q.difficulty === 'Easy' ? 'selected' : ''}>Easy</option>
                        <option value="Medium" ${q.difficulty === 'Medium' ? 'selected' : ''}>Medium</option>
                        <option value="Hard" ${q.difficulty === 'Hard' ? 'selected' : ''}>Hard</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Topic / Chapter</label>
                    <input type="text" class="form-control q-topic-input" value="${escapeHtml(q.topic)}" placeholder="e.g. Unit 2: Sorting">
                </div>
                <div class="form-group">
                    <label>Negative Marks</label>
                    <input type="number" class="form-control q-negative-input" value="${q.negative_marks || 0}" min="0" step="0.25">
                </div>
            `;
            body.appendChild(metaGrid);

            // Statement
            const stmtGroup = document.createElement('div');
            stmtGroup.className = 'form-group';
            stmtGroup.innerHTML = `
                <label>Question Statement <span style="color:var(--danger)">*</span></label>
                <textarea class="form-control q-statement-input" rows="3" placeholder="Enter the complete question statement or scenario...">${escapeHtml(q.statement)}</textarea>
            `;
            body.appendChild(stmtGroup);

            // Optional Image URL
            const imgGroup = document.createElement('div');
            imgGroup.className = 'form-group';
            imgGroup.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <label style="margin:0;">Optional Image / Figure URL</label>
                    <button type="button" class="btn-icon-subtle btn-toggle-img" style="font-size:0.75rem;">${q.image_url ? 'Hide Preview' : 'Preview'}</button>
                </div>
                <input type="text" class="form-control q-image-input" value="${escapeHtml(q.image_url)}" placeholder="https://example.com/figure1.png" style="margin-top:4px;">
                <div class="img-preview-box" style="margin-top:6px; display:${q.image_url ? 'block' : 'none'};">
                    <img src="${escapeHtml(q.image_url)}" alt="Question figure preview" style="max-height:120px; border-radius:6px; border:1px solid #cbd5e1;" onerror="this.style.display='none'">
                </div>
            `;
            body.appendChild(imgGroup);

            // Type-specific answer editors
            const answersShell = document.createElement('div');
            answersShell.className = 'answers-editor-container';
            renderAnswerEditor(answersShell, q, index);
            body.appendChild(answersShell);

            // Explanation & Solution
            const explGroup = document.createElement('div');
            explGroup.className = 'form-group';
            explGroup.innerHTML = `
                <label>Explanation / Marking Solution (Displayed in Answer Key)</label>
                <textarea class="form-control q-expl-input" rows="2" placeholder="Provide step-by-step reasoning or scoring criteria...">${escapeHtml(q.explanation)}</textarea>
            `;
            body.appendChild(explGroup);

            card.appendChild(header);
            card.appendChild(body);
            elements.questionsContainer.appendChild(card);

            // Event bindings for this card
            bindCardEvents(card, index);
        });

        setupDragAndDrop();
    }

    function renderAnswerEditor(container, q, qIndex) {
        container.innerHTML = '';

        if (q.question_type === 'mcq' || q.question_type === 'multiple_select') {
            const isRadio = q.question_type === 'mcq';
            const label = document.createElement('label');
            label.style.fontWeight = '600';
            label.innerHTML = `Answer Options <span style="font-weight:normal; color:var(--text-muted); font-size:0.8rem;">(Select the ${isRadio ? 'single correct radio option' : 'correct checkbox option(s)'})</span>`;
            container.appendChild(label);

            const optionsShell = document.createElement('div');
            optionsShell.className = 'options-editor-shell';

            q.options.forEach((opt, optIndex) => {
                const row = document.createElement('div');
                row.className = `builder-option-row ${opt.is_correct ? 'is-correct' : ''}`;
                row.innerHTML = `
                    <label class="option-correct-picker" title="Mark as correct">
                        <input type="${isRadio ? 'radio' : 'checkbox'}" name="opt_correct_${q.id}" ${opt.is_correct ? 'checked' : ''}>
                    </label>
                    <span style="font-weight:700; color:var(--text-muted); font-size:0.85rem; width:18px;">${String.fromCharCode(65 + optIndex)}.</span>
                    <input type="text" class="option-text-input" value="${escapeHtml(opt.option_text)}" placeholder="Enter option text...">
                    <button type="button" class="btn-option-remove" title="Remove option">&times;</button>
                `;

                // Pick correct
                const pickerInput = row.querySelector('input[type="radio"], input[type="checkbox"]');
                pickerInput.addEventListener('change', function () {
                    if (isRadio) {
                        q.options.forEach((o, i) => o.is_correct = (i === optIndex));
                    } else {
                        opt.is_correct = this.checked;
                    }
                    renderQuestions();
                });

                // Text change
                const txtInput = row.querySelector('.option-text-input');
                txtInput.addEventListener('input', function () {
                    opt.option_text = this.value;
                });

                // Remove option
                const removeBtn = row.querySelector('.btn-option-remove');
                removeBtn.addEventListener('click', function () {
                    if (q.options.length <= 2) {
                        showAlert('Multiple-choice questions require at least two options.', 'warning');
                        return;
                    }
                    q.options.splice(optIndex, 1);
                    renderQuestions();
                });

                optionsShell.appendChild(row);
            });

            const addOptBtn = document.createElement('button');
            addOptBtn.type = 'button';
            addOptBtn.className = 'btn-add-option';
            addOptBtn.textContent = '+ Add Option';
            addOptBtn.addEventListener('click', function () {
                q.options.push({
                    option_text: `Option ${q.options.length + 1}`,
                    is_correct: false,
                });
                renderQuestions();
            });

            container.appendChild(optionsShell);
            container.appendChild(addOptBtn);

        } else if (q.question_type === 'true_false') {
            const label = document.createElement('label');
            label.style.fontWeight = '600';
            label.textContent = 'Correct Answer';
            container.appendChild(label);

            const row = document.createElement('div');
            row.style.display = 'flex';
            row.style.gap = '20px';
            row.style.marginTop = '6px';

            const trueOpt = q.options.find(o => o.option_text === 'True') || { option_text: 'True', is_correct: true };
            const falseOpt = q.options.find(o => o.option_text === 'False') || { option_text: 'False', is_correct: false };
            q.options = [trueOpt, falseOpt];

            row.innerHTML = `
                <label style="display:flex; align-items:center; gap:8px; cursor:pointer; font-weight:600;">
                    <input type="radio" name="tf_${q.id}" value="True" ${trueOpt.is_correct ? 'checked' : ''}> True
                </label>
                <label style="display:flex; align-items:center; gap:8px; cursor:pointer; font-weight:600;">
                    <input type="radio" name="tf_${q.id}" value="False" ${falseOpt.is_correct ? 'checked' : ''}> False
                </label>
            `;

            row.querySelectorAll('input[type="radio"]').forEach(r => {
                r.addEventListener('change', function () {
                    trueOpt.is_correct = (this.value === 'True');
                    falseOpt.is_correct = (this.value === 'False');
                });
            });

            container.appendChild(row);

        } else if (q.question_type === 'fill_blank') {
            const group = document.createElement('div');
            group.className = 'form-group';
            group.innerHTML = `
                <label>Accepted Blank Answer(s) <span style="color:var(--text-muted); font-size:0.8rem;">(Separate acceptable variations with comma)</span></label>
                <input type="text" class="form-control q-corr-text-input" value="${escapeHtml(q.correct_answer_text)}" placeholder="e.g. CPU, Central Processing Unit">
            `;
            group.querySelector('input').addEventListener('input', function () {
                q.correct_answer_text = this.value;
            });
            container.appendChild(group);

        } else if (q.question_type === 'short_answer') {
            const group = document.createElement('div');
            group.className = 'form-group';
            group.innerHTML = `
                <label>Model Answer / Key Phrases Expected</label>
                <input type="text" class="form-control q-corr-text-input" value="${escapeHtml(q.correct_answer_text)}" placeholder="e.g. Principle of Encapsulation, bundling data with methods">
            `;
            group.querySelector('input').addEventListener('input', function () {
                q.correct_answer_text = this.value;
            });
            container.appendChild(group);

        } else if (q.question_type === 'long_answer') {
            const group = document.createElement('div');
            group.className = 'form-group';
            group.innerHTML = `
                <label>Evaluation Rubric & Essential Concepts</label>
                <textarea class="form-control q-corr-text-input" rows="3" placeholder="Outline mandatory technical components, diagrams required, and point distribution...">${escapeHtml(q.correct_answer_text)}</textarea>
            `;
            group.querySelector('textarea').addEventListener('input', function () {
                q.correct_answer_text = this.value;
            });
            container.appendChild(group);

        } else if (q.question_type === 'numerical') {
            const group = document.createElement('div');
            group.className = 'form-group';
            group.innerHTML = `
                <label>Correct Numerical Value</label>
                <input type="number" step="any" class="form-control q-corr-text-input" value="${escapeHtml(q.correct_answer_text)}" placeholder="e.g. 42 or 3.1415">
            `;
            group.querySelector('input').addEventListener('input', function () {
                q.correct_answer_text = this.value;
            });
            container.appendChild(group);
        }
    }

    function bindCardEvents(card, index) {
        const q = questions[index];

        // Type Change
        const typeSelect = card.querySelector('.q-type-select');
        if (typeSelect) {
            typeSelect.addEventListener('change', function () {
                changeQuestionType(index, this.value);
            });
        }

        // Marks
        const marksInput = card.querySelector('.q-marks-input');
        if (marksInput) {
            marksInput.addEventListener('input', function () {
                q.marks = parseFloat(this.value) || 0;
                updateSummary();
            });
        }

        // Difficulty
        const diffSelect = card.querySelector('.q-diff-select');
        if (diffSelect) {
            diffSelect.addEventListener('change', function () {
                q.difficulty = this.value;
            });
        }

        // Topic
        const topicInput = card.querySelector('.q-topic-input');
        if (topicInput) {
            topicInput.addEventListener('input', function () {
                q.topic = this.value;
            });
        }

        // Negative marks
        const negInput = card.querySelector('.q-negative-input');
        if (negInput) {
            negInput.addEventListener('input', function () {
                q.negative_marks = parseFloat(this.value) || 0;
            });
        }

        // Statement
        const stmtInput = card.querySelector('.q-statement-input');
        if (stmtInput) {
            stmtInput.addEventListener('input', function () {
                q.statement = this.value;
            });
        }

        // Image URL & Toggle
        const imgInput = card.querySelector('.q-image-input');
        const imgToggle = card.querySelector('.btn-toggle-img');
        const imgPreview = card.querySelector('.img-preview-box');
        if (imgInput && imgPreview) {
            imgInput.addEventListener('input', function () {
                q.image_url = this.value.trim();
                const imgEl = imgPreview.querySelector('img');
                if (imgEl && q.image_url) {
                    imgEl.src = q.image_url;
                    imgEl.style.display = 'block';
                    imgPreview.style.display = 'block';
                } else {
                    imgPreview.style.display = 'none';
                }
            });
        }
        if (imgToggle && imgPreview) {
            imgToggle.addEventListener('click', function () {
                const isShown = imgPreview.style.display !== 'none';
                imgPreview.style.display = isShown ? 'none' : 'block';
                imgToggle.textContent = isShown ? 'Preview' : 'Hide Preview';
            });
        }

        // Explanation
        const explInput = card.querySelector('.q-expl-input');
        if (explInput) {
            explInput.addEventListener('input', function () {
                q.explanation = this.value;
            });
        }

        // Actions
        const btnUp = card.querySelector('.btn-move-up');
        if (btnUp) btnUp.addEventListener('click', () => moveQuestion(index, -1));

        const btnDown = card.querySelector('.btn-move-down');
        if (btnDown) btnDown.addEventListener('click', () => moveQuestion(index, 1));

        const btnDup = card.querySelector('.btn-duplicate');
        if (btnDup) btnDup.addEventListener('click', () => duplicateQuestion(index));

        const btnDel = card.querySelector('.btn-delete');
        if (btnDel) btnDel.addEventListener('click', () => deleteQuestion(index));
    }

    // ==========================================
    // Drag-and-Drop Reordering
    // ==========================================
    let draggedIndex = null;

    function setupDragAndDrop() {
        const cards = elements.questionsContainer.querySelectorAll('.builder-question-card');
        cards.forEach(card => {
            card.addEventListener('dragstart', handleDragStart);
            card.addEventListener('dragover', handleDragOver);
            card.addEventListener('dragleave', handleDragLeave);
            card.addEventListener('drop', handleDrop);
            card.addEventListener('dragend', handleDragEnd);
        });
    }

    function handleDragStart(e) {
        draggedIndex = parseInt(this.dataset.index, 10);
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', draggedIndex);
        this.classList.add('is-dragging');
    }

    function handleDragOver(e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        this.classList.add('drag-over');
    }

    function handleDragLeave() {
        this.classList.remove('drag-over');
    }

    function handleDrop(e) {
        e.preventDefault();
        this.classList.remove('drag-over');
        const targetIndex = parseInt(this.dataset.index, 10);
        if (draggedIndex !== null && draggedIndex !== targetIndex) {
            const item = questions.splice(draggedIndex, 1)[0];
            questions.splice(targetIndex, 0, item);
            renderQuestions();
            updateSummary();
        }
    }

    function handleDragEnd() {
        this.classList.remove('is-dragging');
        draggedIndex = null;
        elements.questionsContainer.querySelectorAll('.builder-question-card').forEach(c => {
            c.classList.remove('drag-over');
        });
    }

    // ==========================================
    // Question Bank Import
    // ==========================================
    async function openBankModal() {
        if (!elements.bankModal) return;
        elements.bankModal.style.display = 'flex';

        if (bankCache.length === 0) {
            elements.bankQuestionsList.innerHTML = '<div style="padding:2rem; text-align:center; color:var(--text-muted);">Loading questions from repository...</div>';
            try {
                const res = await fetch('/admin/api/question-bank/questions');
                const data = await res.json();
                bankCache = data.questions || [];
                populateBankSubjects();
            } catch (err) {
                elements.bankQuestionsList.innerHTML = '<div style="padding:2rem; text-align:center; color:var(--danger);">Failed to load questions from bank.</div>';
                return;
            }
        }
        renderBankList();
    }

    function closeBankModal() {
        if (elements.bankModal) elements.bankModal.style.display = 'none';
    }

    function populateBankSubjects() {
        if (!elements.bankSubjectFilter) return;
        const subjects = new Set();
        bankCache.forEach(q => {
            if (q.subject_name) subjects.add(q.subject_name);
        });
        elements.bankSubjectFilter.innerHTML = '<option value="">All Subjects</option>';
        subjects.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.toLowerCase();
            opt.textContent = s;
            elements.bankSubjectFilter.appendChild(opt);
        });
    }

    function renderBankList() {
        if (!elements.bankQuestionsList) return;
        const search = (elements.bankSearchInput?.value || '').toLowerCase().trim();
        const subject = (elements.bankSubjectFilter?.value || '').toLowerCase().trim();

        const filtered = bankCache.filter(q => {
            if (search && !q.statement.toLowerCase().includes(search) && !q.topic.toLowerCase().includes(search)) return false;
            if (subject && (!q.subject_name || q.subject_name.toLowerCase() !== subject)) return false;
            return true;
        });

        if (filtered.length === 0) {
            elements.bankQuestionsList.innerHTML = '<div style="padding:2rem; text-align:center; color:var(--text-muted);">No matching questions found in question bank.</div>';
            return;
        }

        elements.bankQuestionsList.innerHTML = '';
        filtered.forEach(q => {
            const card = document.createElement('div');
            card.className = 'bank-item-card';
            card.style.border = '1px solid var(--border-color)';
            card.style.borderRadius = 'var(--radius-md)';
            card.style.padding = '1rem';
            card.style.marginBottom = '10px';
            card.style.background = '#ffffff';

            card.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:6px;">
                    <div>
                        <span class="badge badge-neutral" style="font-size:0.75rem;">${escapeHtml(q.subject_name || 'General')}</span>
                        <span class="badge badge-neutral" style="font-size:0.75rem;">${getTypeLabel(q.question_type)}</span>
                        <span class="badge badge-neutral" style="font-size:0.75rem;">${q.difficulty}</span>
                    </div>
                    <div>
                        <strong style="color:var(--primary); font-size:0.88rem;">${q.marks} Marks</strong>
                    </div>
                </div>
                <div style="font-size:0.92rem; font-weight:600; color:var(--text-primary); margin-bottom:8px;">
                    ${escapeHtml(q.statement)}
                </div>
                ${q.options && q.options.length ? `
                    <div style="font-size:0.8rem; color:var(--text-muted); margin-bottom:8px;">
                        ${q.options.map(o => `${escapeHtml(o.option_text)} ${o.is_correct ? '&#x2714;' : ''}`).join(' &bull; ')}
                    </div>
                ` : ''}
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:0.75rem; color:var(--text-muted);">${escapeHtml(q.topic || q.exam_name || '')}</span>
                    <button type="button" class="btn-primary btn-import-q" style="padding:0.35rem 0.85rem; font-size:0.8rem;">+ Import to Paper</button>
                </div>
            `;

            card.querySelector('.btn-import-q').addEventListener('click', function () {
                importFromBank(q);
                this.textContent = 'Imported!';
                this.style.background = 'var(--success)';
                setTimeout(() => {
                    this.textContent = '+ Import to Paper';
                    this.style.background = '';
                }, 1500);
            });

            elements.bankQuestionsList.appendChild(card);
        });
    }

    function importFromBank(bankQ) {
        const q = {
            id: 'q_' + Date.now() + '_' + Math.random().toString(36).substring(2, 7),
            statement: bankQ.statement || '',
            question_type: bankQ.question_type || 'mcq',
            marks: parseFloat(bankQ.marks) || 1,
            difficulty: bankQ.difficulty || 'Medium',
            topic: bankQ.topic || '',
            image_url: bankQ.image_url || '',
            explanation: bankQ.explanation || '',
            negative_marks: parseFloat(bankQ.negative_marks) || 0,
            correct_answer_text: bankQ.correct_answer_text || '',
            options: (bankQ.options || []).map(o => ({
                option_text: o.option_text,
                is_correct: Boolean(o.is_correct),
            })),
        };

        questions.push(q);
        renderQuestions();
        updateSummary();
        showAlert(`Imported question "${q.statement.substring(0, 35)}..." from Question Bank.`, 'success');
    }

    // ==========================================
    // Question Paper Preview Modal
    // ==========================================
    function openPreviewModal() {
        if (!elements.previewModal || !elements.previewContent) return;

        // Collect metadata
        const title = elements.titleInput?.value.trim() || 'Untitled Examination Assessment';
        let subject = elements.subjectSelect?.value || 'General Examination';
        if (subject === '__custom__' && elements.subjectCustom) {
            subject = elements.subjectCustom.value.trim() || 'General';
        }
        const dept = elements.deptInput?.value.trim() || 'Academic Faculty';
        const sem = elements.semSelect?.value || 'Current Semester';
        const year = elements.yearInput?.value.trim() || '2025-2026';
        const duration = elements.durationInput?.value || '45';
        const totalMarks = elements.totalMarksInput?.value || '0';
        const instructions = elements.instructionsInput?.value.trim() || '';

        let questionsHtml = '';
        questions.forEach((q, idx) => {
            let optionsHtml = '';
            if (q.options && q.options.length > 0) {
                optionsHtml = `
                    <ol class="preview-options-list" type="A">
                        ${q.options.map(opt => `
                            <li class="${opt.is_correct ? 'correct-highlight answer-key-item' : ''}">
                                ${escapeHtml(opt.option_text)}
                                ${opt.is_correct ? `<span class="answer-key-badge" style="font-size:0.75rem; background:#dcfce7; color:#166534; padding:2px 6px; border-radius:4px; margin-left:6px; font-weight:bold; display:${previewAnswerKeyVisible ? 'inline-block' : 'none'};">(Correct)</span>` : ''}
                            </li>
                        `).join('')}
                    </ol>
                `;
            } else if (q.question_type === 'fill_blank') {
                optionsHtml = `<div style="margin-top:8px; font-style:italic; color:#475569;">[Fill in the blank: ______________________________]</div>`;
            } else if (q.question_type === 'numerical') {
                optionsHtml = `<div style="margin-top:8px; font-style:italic; color:#475569;">[Numerical Answer: ______________________________]</div>`;
            } else if (q.question_type === 'short_answer' || q.question_type === 'long_answer') {
                optionsHtml = `<div style="margin-top:12px; border:1px dashed #cbd5e1; height:${q.question_type === 'long_answer' ? '100px' : '50px'}; border-radius:4px; background:#f8fafc; padding:8px; font-size:0.85rem; color:#94a3b8;">[Student Answer Response Space]</div>`;
            }

            questionsHtml += `
                <div class="preview-question-item">
                    <div class="preview-q-top">
                        <span><strong>Q${idx + 1}.</strong> ${escapeHtml(q.statement || '(No statement provided)')}</span>
                        <span class="preview-q-marks">[${q.marks} Marks]</span>
                    </div>
                    ${q.image_url ? `<div style="margin:10px 0;"><img src="${escapeHtml(q.image_url)}" style="max-height:160px; border:1px solid #cbd5e1; border-radius:4px;"></div>` : ''}
                    ${optionsHtml}
                    <div class="preview-answer-key-box modal-answer-key-section" style="display:${previewAnswerKeyVisible ? 'block' : 'none'}; margin-top:10px;">
                        <strong>Evaluation Scheme / Answer Key:</strong>
                        ${q.correct_answer_text ? `<div><strong>Correct Answer:</strong> ${escapeHtml(q.correct_answer_text)}</div>` : ''}
                        ${q.explanation ? `<div><strong>Explanation:</strong> ${escapeHtml(q.explanation)}</div>` : ''}
                        <div style="font-size:0.78rem; color:#64748b; margin-top:4px;">
                            Type: ${getTypeLabel(q.question_type)} &bull; Difficulty: ${q.difficulty} ${q.topic ? `&bull; Topic: ${escapeHtml(q.topic)}` : ''}
                        </div>
                    </div>
                </div>
            `;
        });

        elements.previewContent.innerHTML = `
            <div class="exam-paper-preview">
                <div class="exam-paper-header">
                    <h1 class="institution-heading">EXAMSHIELD ACADEMIC PORTAL</h1>
                    <div style="font-size:0.95rem; font-weight:600; text-transform:uppercase; letter-spacing:0.05em; color:#475569;">
                        Department of ${escapeHtml(dept)} &bull; Academic Year ${escapeHtml(year)}
                    </div>
                    <h2 class="paper-title" style="margin-top:10px;">${escapeHtml(title)}</h2>
                    <div class="paper-meta-row">
                        <div><strong>Subject:</strong> ${escapeHtml(subject)} (${escapeHtml(sem)})</div>
                        <div><strong>Duration:</strong> ${escapeHtml(duration)} Minutes</div>
                        <div><strong>Maximum Marks:</strong> ${escapeHtml(totalMarks)}</div>
                    </div>
                </div>

                ${instructions ? `
                    <div class="paper-instructions-box">
                        <h4>General Instructions to Candidates:</h4>
                        <div style="white-space:pre-line;">${escapeHtml(instructions)}</div>
                    </div>
                ` : ''}

                <div class="preview-questions-body">
                    ${questionsHtml}
                </div>

                <div style="text-align:center; margin-top:2.5rem; border-top:1px solid #cbd5e1; padding-top:1rem; font-size:0.85rem; color:#64748b;">
                    &mdash; END OF QUESTION PAPER &mdash;
                </div>
            </div>
        `;

        elements.previewModal.style.display = 'flex';
    }

    function closePreviewModal() {
        if (elements.previewModal) elements.previewModal.style.display = 'none';
    }

    function togglePreviewModalAnswerKey() {
        previewAnswerKeyVisible = !previewAnswerKeyVisible;
        const keySections = elements.previewModal.querySelectorAll('.modal-answer-key-section');
        const badges = elements.previewModal.querySelectorAll('.answer-key-badge');
        keySections.forEach(el => el.style.display = previewAnswerKeyVisible ? 'block' : 'none');
        badges.forEach(el => el.style.display = previewAnswerKeyVisible ? 'inline-block' : 'none');
    }

    // ==========================================
    // Save, Validate & Publish
    // ==========================================
    async function saveExam(status) {
        clearAlert();

        const title = elements.titleInput?.value.trim();
        if (!title) {
            showAlert('Please specify an Examination Title before proceeding.', 'danger');
            elements.titleInput?.focus();
            return;
        }

        let subject = elements.subjectSelect?.value || '';
        if (subject === '__custom__' && elements.subjectCustom) {
            subject = elements.subjectCustom.value.trim();
        }
        if (!subject) {
            showAlert('Please select or specify a Subject for this examination.', 'danger');
            elements.subjectSelect?.focus();
            return;
        }

        const dept = elements.deptInput?.value.trim() || '';
        const sem = elements.semSelect?.value || '';
        const year = elements.yearInput?.value.trim() || '';
        const desc = elements.descInput?.value.trim() || '';
        const instructions = elements.instructionsInput?.value.trim() || '';
        const duration = parseInt(elements.durationInput?.value || 30, 10);
        const passMarks = parseFloat(elements.passMarksInput?.value || 0);
        const negativeMarks = parseFloat(elements.negativeMarkInput?.value || 0);
        const randQ = Boolean(elements.randQCheck?.checked);
        const randOpt = Boolean(elements.randOptCheck?.checked);
        const multiAttempts = Boolean(elements.multiAttemptCheck?.checked);
        const startTime = elements.startTimeInput?.value || null;
        const endTime = elements.endTimeInput?.value || null;

        if (duration <= 0) {
            showAlert('Examination duration must be at least 1 minute.', 'danger');
            elements.durationInput?.focus();
            return;
        }

        // Strict validation for published / scheduled
        if (status === 'published' || status === 'scheduled') {
            if (questions.length === 0) {
                showAlert('At least one question is required to publish or schedule an examination.', 'danger');
                return;
            }

            if (status === 'scheduled') {
                if (!startTime) {
                    showAlert('Start date and time are required for scheduled examinations.', 'danger');
                    elements.startTimeInput?.focus();
                    return;
                }
                if (startTime && endTime && startTime > endTime) {
                    showAlert('Scheduled end time cannot be earlier than start time.', 'danger');
                    elements.endTimeInput?.focus();
                    return;
                }
            }

            // Validate questions
            for (let i = 0; i < questions.length; i++) {
                const q = questions[i];
                if (!q.statement || !q.statement.trim()) {
                    showAlert(`Question #${i + 1} statement is empty. Please enter the question text.`, 'danger');
                    const card = document.getElementById(q.id);
                    if (card) {
                        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        card.querySelector('.q-statement-input')?.focus();
                    }
                    return;
                }

                if (parseFloat(q.marks) <= 0) {
                    showAlert(`Question #${i + 1} marks must be greater than zero.`, 'danger');
                    return;
                }

                if (q.question_type === 'mcq' || q.question_type === 'multiple_select' || q.question_type === 'true_false') {
                    if (!q.options || q.options.length < 2) {
                        showAlert(`Question #${i + 1} requires at least two answer options.`, 'danger');
                        return;
                    }
                    const hasCorrect = q.options.some(o => o.is_correct);
                    if (!hasCorrect) {
                        showAlert(`Question #${i + 1} does not have any correct answer option selected.`, 'danger');
                        const card = document.getElementById(q.id);
                        if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        return;
                    }
                } else if (q.question_type === 'fill_blank' || q.question_type === 'short_answer' || q.question_type === 'numerical') {
                    if (!q.correct_answer_text || !q.correct_answer_text.trim()) {
                        showAlert(`Question #${i + 1} requires a correct answer specification.`, 'danger');
                        const card = document.getElementById(q.id);
                        if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        return;
                    }
                }
            }
        }

        let totalMarks = 0;
        questions.forEach(q => totalMarks += (parseFloat(q.marks) || 0));
        if (totalMarks <= 0 && status !== 'draft') {
            totalMarks = 10;
        }

        if (passMarks > totalMarks) {
            showAlert(`Passing marks (${passMarks}) cannot be greater than total examination marks (${totalMarks}).`, 'danger');
            elements.passMarksInput?.focus();
            return;
        }

        const payload = {
            csrf_token: elements.csrfToken,
            title: title,
            subject_name: subject,
            department: dept,
            semester: sem,
            academic_year: year,
            description: desc,
            instructions: instructions,
            duration_minutes: duration,
            max_marks: totalMarks,
            pass_marks: passMarks,
            negative_marking: negativeMarks,
            randomize_questions: randQ,
            randomize_options: randOpt,
            allow_multiple_attempts: multiAttempts,
            start_time: startTime,
            end_time: endTime,
            status: status,
            questions: questions.map((q, idx) => ({
                statement: q.statement,
                question_type: q.question_type,
                marks: parseFloat(q.marks) || 1,
                difficulty: q.difficulty,
                topic: q.topic,
                image_url: q.image_url,
                explanation: q.explanation,
                negative_marks: parseFloat(q.negative_marks) || 0,
                correct_answer_text: q.correct_answer_text,
                sort_order: idx,
                options: (q.options || []).map((opt, optIdx) => ({
                    option_text: opt.option_text,
                    is_correct: Boolean(opt.is_correct),
                    sort_order: optIdx,
                })),
            })),
        };

        // Submit to API
        try {
            showAlert('Saving examination question paper...', 'info');
            const res = await fetch('/admin/exams/create', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': elements.csrfToken,
                },
                body: JSON.stringify(payload),
            });

            const data = await res.json();
            if (res.ok && data.success) {
                showAlert(data.message || 'Question paper saved successfully!', 'success');
                setTimeout(() => {
                    window.location.href = data.redirect_url || '/admin/exams';
                }, 1000);
            } else {
                showAlert(data.error || 'Failed to save question paper. Please verify all entries.', 'danger');
            }
        } catch (err) {
            showAlert('Network or server error while saving question paper. Please check connection and try again.', 'danger');
        }
    }

    // ==========================================
    // Helpers
    // ==========================================
    function getTypeLabel(type) {
        const map = {
            mcq: 'MCQ',
            multiple_select: 'MSQ (Multi-Select)',
            true_false: 'True / False',
            fill_blank: 'Fill in Blanks',
            short_answer: 'Short Answer',
            long_answer: 'Long Answer',
            numerical: 'Numerical',
        };
        return map[type] || (type || 'MCQ').toUpperCase();
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function showAlert(msg, level = 'info') {
        if (!elements.alertBanner) return;
        elements.alertBanner.textContent = msg;
        elements.alertBanner.className = `alert-banner alert-${level}`;
        elements.alertBanner.style.display = 'block';
        elements.alertBanner.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    function clearAlert() {
        if (elements.alertBanner) {
            elements.alertBanner.style.display = 'none';
            elements.alertBanner.textContent = '';
        }
    }

})();
