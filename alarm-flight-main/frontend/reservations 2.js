(() => {
    const nav = document.querySelector(".nav");

    const openButton = document.createElement("button");
    openButton.type = "button";
    openButton.className = "secondary";
    openButton.textContent = "내 예약";
    nav.append(openButton);

    const dialog = document.createElement("dialog");
    dialog.id = "reservationsDialog";
    dialog.setAttribute("aria-labelledby", "reservationsTitle");
    dialog.style.width = "min(640px, 92vw)";

    dialog.innerHTML = `
        <h2 id="reservationsTitle">내 예약 내역</h2>

        <p class="muted">
            모의 예약 내역입니다. 실제 결제나 환불은 발생하지 않습니다.
        </p>

        <p id="reservationsMessage"
           role="status"
           aria-live="polite"></p>

        <div id="reservationsList"></div>

        <div style="display:flex;gap:10px;margin-top:20px">
            <button id="reservationsRefresh"
                    class="secondary" type="button">
                새로고침
            </button>
            <button id="reservationsClose"
                    class="secondary" type="button">
                닫기
            </button>
        </div>
    `;

    document.body.append(dialog);

    const list = document.getElementById("reservationsList");
    const notice = document.getElementById("reservationsMessage");
    const refreshButton = document.getElementById("reservationsRefresh");
    const closeButton = document.getElementById("reservationsClose");

    let busy = false;

    const statusNames = {
        CONFIRMED: "예약 완료",
        CAPTCHA_REQUIRED: "추가 확인 대기",
        BLOCKED: "예약 차단",
        CANCELLED: "예약 취소"
    };

    const statusColors = {
        CONFIRMED: "#15803d",
        CAPTCHA_REQUIRED: "#b45309",
        BLOCKED: "#b42318",
        CANCELLED: "#64748b"
    };

    function makeElement(tag, text, className = "") {
        const node = document.createElement(tag);
        node.textContent = text;
        node.className = className;
        return node;
    }

    function showMessage(text, error = false) {
        notice.textContent = text;
        notice.style.color = error ? "#b42318" : "#475569";
    }

    function setBusy(value) {
        busy = value;
        refreshButton.disabled = value;
        closeButton.disabled = value;

        list.querySelectorAll("button").forEach(button => {
            button.disabled = value;
        });
    }

    async function request(path, options = {}) {
        const response = await fetch(path, {
            cache: "no-store",
            ...options
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(
                typeof data.detail === "string"
                    ? data.detail
                    : "요청을 처리하지 못했습니다."
            );
        }

        return data;
    }

    function formatMoney(value) {
        return Number(value).toLocaleString("ko-KR") + "원";
    }

    function formatTime(value) {
        return value
            ? String(value).replace("T", " ").slice(0, 16)
            : "미정";
    }

    function renderReservations(reservations, flights) {
        list.replaceChildren();

        if (reservations.length === 0) {
            list.append(
                makeElement("p", "아직 예약 내역이 없습니다.", "empty")
            );
            return;
        }

        const flightMap = new Map(
            flights.map(flight => [flight.id, flight])
        );

        const sorted = [...reservations].sort(
            (a, b) => b.id - a.id
        );

        for (const reservation of sorted) {
            const flight = flightMap.get(reservation.flight_id);

            const card = document.createElement("article");
            card.style.cssText = `
                border:1px solid #dce4ef;
                border-radius:12px;
                padding:18px;
                margin-bottom:14px;
            `;

            const status = makeElement(
                "p",
                statusNames[reservation.status] || reservation.status
            );

            status.style.color =
                statusColors[reservation.status] || "#475569";
            status.style.fontWeight = "700";
            status.style.marginTop = "0";

            card.append(
                status,
                makeElement("h3", `예약번호 ${reservation.id}`)
            );

            if (flight) {
                card.append(
                    makeElement(
                        "p",
                        `${flight.flight_no} · ${flight.departure_airport} → ${flight.arrival_airport}`
                    ),
                    makeElement(
                        "p",
                        `출발: ${formatTime(flight.departure_time)}`,
                        "muted"
                    )
                );
            } else {
                card.append(
                    makeElement(
                        "p",
                        `항공편 번호(ID): ${reservation.flight_id}`,
                        "muted"
                    )
                );
            }

            card.append(
                makeElement(
                    "p",
                    `탑승 인원: ${reservation.seat_count}명`
                ),
                makeElement(
                    "p",
                    `예약 요청 금액: ${formatMoney(reservation.total_price)}`
                ),
                makeElement(
                    "p",
                    reservation.coupon_id
                        ? `선택한 쿠폰 번호: ${reservation.coupon_id}`
                        : "선택한 쿠폰 없음",
                    "muted"
                ),
                makeElement(
                    "p",
                    `요청 시각: ${formatTime(reservation.created_at)}`,
                    "muted"
                )
            );

            if (reservation.status === "BLOCKED") {
                card.append(
                    makeElement(
                        "p",
                        "예약이 차단되어 좌석과 쿠폰은 사용되지 않았습니다.",
                        "muted"
                    )
                );
            }

            if (reservation.status === "CAPTCHA_REQUIRED") {
                card.append(
                    makeElement(
                        "p",
                        "추가 확인이 완료되지 않았습니다. 좌석은 확보되지 않은 상태입니다.",
                        "muted"
                    )
                );
            }

            if (
                reservation.status === "CONFIRMED" ||
                reservation.status === "CAPTCHA_REQUIRED"
            ) {
                const cancelButton = makeElement(
                    "button",
                    reservation.status === "CONFIRMED"
                        ? "예약 취소"
                        : "대기 요청 취소",
                    "secondary"
                );

                cancelButton.type = "button";

                cancelButton.addEventListener("click", () => {
                    cancelReservation(reservation);
                });

                card.append(cancelButton);
            }

            list.append(card);
        }
    }

    async function loadReservations() {
        const userId = sessionStorage.getItem("alarmFlightUserId");

        list.replaceChildren();

        if (!userId) {
            showMessage("상단의 회원가입을 먼저 진행해 주세요.");
            return;
        }

        showMessage("예약 내역을 불러오고 있습니다.");

        const reservations = await request(
            `/users/${encodeURIComponent(userId)}/reservations`
        );

        if (!Array.isArray(reservations)) {
            throw new Error("예약 목록의 응답 형식이 올바르지 않습니다.");
        }

        // 항공편 조회에 실패해도 예약 내역 자체는 보여줍니다.
        let flights = [];
        let flightLookupFailed = false;

        try {
            const data = await request("/flights");

            if (!Array.isArray(data)) {
                throw new Error("항공편 목록 형식 오류");
            }

            flights = data;
        } catch (error) {
            flightLookupFailed = true;
        }

        renderReservations(reservations, flights);

        showMessage(
            `총 ${reservations.length}건의 예약 내역이 있습니다.` +
            (flightLookupFailed
                ? " 항공편 상세 정보는 불러오지 못했습니다."
                : "")
        );
    }

    async function refreshReservations() {
        if (busy) return;

        setBusy(true);

        try {
            await loadReservations();
        } catch (error) {
            showMessage(`조회 실패: ${error.message}`, true);
        } finally {
            setBusy(false);
        }
    }

    async function cancelReservation(reservation) {
        if (busy) return;

        const description =
            reservation.status === "CONFIRMED"
                ? "취소하면 좌석이 반환되고, 사용한 쿠폰의 사용 처리가 해제됩니다."
                : "추가 확인 대기 중인 요청을 취소합니다.";

        const confirmed = window.confirm(
            `예약번호 ${reservation.id}를 취소할까요?\n\n${description}`
        );

        if (!confirmed) return;

        setBusy(true);
        showMessage("취소 요청을 처리하고 있습니다.");

        try {
            await request(
                `/reservations/${reservation.id}/cancel`,
                { method: "POST" }
            );
        } catch (error) {
            showMessage(
                `취소 결과 확인 필요: ${error.message} ` +
                "통신 오류라면 이미 취소됐을 수 있으니, 새로고침으로 상태를 확인해 주세요.",
                true
            );
            setBusy(false);
            return;
        }

        try {
            await loadReservations();
            showMessage(`예약번호 ${reservation.id}가 취소되었습니다.`);
        } catch (error) {
            showMessage(
                "취소는 완료됐지만 목록을 다시 불러오지 못했습니다. 새로고침을 눌러 주세요.",
                true
            );
        } finally {
            setBusy(false);
        }

        // 검색 화면의 잔여 좌석도 갱신합니다.
        if (typeof window.searchFlights === "function") {
            window.searchFlights().catch(() => {});
        }
    }

    openButton.addEventListener("click", () => {
        dialog.showModal();
        refreshReservations();
    });

    refreshButton.addEventListener("click", refreshReservations);

    closeButton.addEventListener("click", () => {
        dialog.close();
    });

    dialog.addEventListener("cancel", event => {
        if (busy) event.preventDefault();
    });
})();