/**
 * Examination Management Interactivity
 * Handles dropdown actions, status changes, scheduling modal, duplication, and cancellation
 */

function getCsrfToken() {
    const input = document.getElementById('adminCsrfToken');
    if (input && input.value) return input.value;
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute('content')) return meta.getAttribute('content');
    return window.ADMIN_CSRF_TOKEN || '';
}

function showFeedback(message, isError = false) {
    const banner = document.getElementById('action-feedback-banner');
    if (!banner) {
        alert(message);
        return;
    }
    banner.style.display = 'block';
    if (isError) {
        banner.style.background = '#fef2f2';
        banner.style.color = '#991b1b';
        banner.style.border = '1px solid #fecaca';
    } else {
        banner.style.background = '#ecfdf5';
        banner.style.color = '#065f46';
        banner.style.border = '1px solid #a7f3d0';
    }
    banner.textContent = message;
    window.scrollTo({ top: 0, behavior: 'smooth' });
    setTimeout(() => {
        banner.style.display = 'none';
    }, 6000);
}

// Action menu toggling
function toggleExamMenu(btn, event) {
    event.stopPropagation();
    const menu = btn.parentElement.querySelector('.exam-actions-menu');
    if (!menu) return;

    // Close other menus
    document.querySelectorAll('.exam-actions-menu').forEach(m => {
        if (m !== menu) m.style.display = 'none';
    });

    const isVisible = menu.style.display === 'block';
    menu.style.display = isVisible ? 'none' : 'block';
    btn.setAttribute('aria-expanded', !isVisible);
}

document.addEventListener('click', () => {
    document.querySelectorAll('.exam-actions-menu').forEach(menu => {
        menu.style.display = 'none';
    });
});

// Quick Status Changes (Live, Completed, Archived)
async function triggerStatusChange(examId, newStatus, confirmMsg) {
    if (confirmMsg && !confirm(confirmMsg)) {
        return;
    }

    try {
        const response = await fetch(`/admin/exams/${examId}/status`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken(),
                'Accept': 'application/json',
            },
            body: JSON.stringify({ status: newStatus }),
        });

        const data = await response.json();
        if (response.ok && data.success) {
            window.location.reload();
        } else {
            showFeedback(data.error || 'Failed to update examination status.', true);
        }
    } catch (err) {
        showFeedback('A network error occurred while updating status.', true);
    }
}

// Duplication
async function triggerDuplicate(examId, examTitle) {
    if (!confirm(`Duplicate "${examTitle}"? A draft copy will be created with all its questions and options.`)) {
        return;
    }

    try {
        const response = await fetch(`/admin/exams/${examId}/duplicate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken(),
                'Accept': 'application/json',
            },
            body: JSON.stringify({}),
        });

        const data = await response.json();
        if (response.ok && data.success) {
            window.location.reload();
        } else {
            showFeedback(data.error || 'Failed to duplicate examination.', true);
        }
    } catch (err) {
        showFeedback('A network error occurred while duplicating examination.', true);
    }
}

// Scheduling Modal
function openScheduleModal(examId, title, startTime, endTime) {
    const modal = document.getElementById('scheduleExamModal');
    if (!modal) return;
    document.getElementById('scheduleExamId').value = examId;
    document.getElementById('scheduleExamTitle').textContent = `"${title}"`;
    
    // Format times for datetime-local (YYYY-MM-DDTHH:MM)
    if (startTime) {
        document.getElementById('scheduleStartTime').value = startTime.substring(0, 16);
    } else {
        const now = new Date();
        document.getElementById('scheduleStartTime').value = now.toISOString().substring(0, 16);
    }
    if (endTime) {
        document.getElementById('scheduleEndTime').value = endTime.substring(0, 16);
    } else {
        const later = new Date(Date.now() + 2 * 60 * 60 * 1000);
        document.getElementById('scheduleEndTime').value = later.toISOString().substring(0, 16);
    }

    const err = document.getElementById('scheduleModalError');
    if (err) err.style.display = 'none';

    modal.style.display = 'flex';
}

function closeScheduleModal() {
    const modal = document.getElementById('scheduleExamModal');
    if (modal) modal.style.display = 'none';
}

async function handleScheduleSubmit(e) {
    e.preventDefault();
    const examId = document.getElementById('scheduleExamId').value;
    const startTime = document.getElementById('scheduleStartTime').value;
    const endTime = document.getElementById('scheduleEndTime').value;
    const errBox = document.getElementById('scheduleModalError');

    if (!startTime || !endTime) {
        if (errBox) {
            errBox.textContent = 'Please choose both start and end times.';
            errBox.style.display = 'block';
        }
        return;
    }

    if (startTime >= endTime) {
        if (errBox) {
            errBox.textContent = 'Scheduled end time must be later than start time.';
            errBox.style.display = 'block';
        }
        return;
    }

    try {
        const response = await fetch(`/admin/exams/${examId}/schedule`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken(),
                'Accept': 'application/json',
            },
            body: JSON.stringify({
                start_time: startTime,
                end_time: endTime,
            }),
        });

        const data = await response.json();
        if (response.ok && data.success) {
            closeScheduleModal();
            window.location.reload();
        } else {
            if (errBox) {
                errBox.textContent = data.error || 'Failed to schedule examination.';
                errBox.style.display = 'block';
            }
        }
    } catch (err) {
        if (errBox) {
            errBox.textContent = 'Network error while scheduling.';
            errBox.style.display = 'block';
        }
    }
}

// Cancel Modal
function openCancelModal(examId, title) {
    const modal = document.getElementById('cancelExamModal');
    if (!modal) return;
    document.getElementById('cancelExamId').value = examId;
    document.getElementById('cancelExamTitle').textContent = `"${title}"`;
    document.getElementById('cancelReason').value = '';
    modal.style.display = 'flex';
}

function closeCancelModal() {
    const modal = document.getElementById('cancelExamModal');
    if (modal) modal.style.display = 'none';
}

async function handleCancelSubmit(e) {
    e.preventDefault();
    const examId = document.getElementById('cancelExamId').value;
    const reason = document.getElementById('cancelReason').value;

    try {
        const response = await fetch(`/admin/exams/${examId}/status`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken(),
                'Accept': 'application/json',
            },
            body: JSON.stringify({
                status: 'cancelled',
                reason: reason,
            }),
        });

        const data = await response.json();
        if (response.ok && data.success) {
            closeCancelModal();
            window.location.reload();
        } else {
            alert(data.error || 'Failed to cancel examination.');
        }
    } catch (err) {
        alert('Network error while cancelling examination.');
    }
}
