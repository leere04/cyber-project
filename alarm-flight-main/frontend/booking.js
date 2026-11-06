(() => {
    const dialog = document.getElementById("detailDialog");
    const content = document.getElementById("detailContent");

    const money = value =>
        Number(value).toLocaleString("ko-KR") + "원";

    let currentFlight = null;
    let coupons = [];
    let reservationId = null;
    let busy = false;
    let loadVersion = 0;

    const panel = document.createElement("section");
    panel.style.cssText =
        "margin-top:20px;padding-top:20px;border-top:1px solid #dce4ef";

    // 고정된 화면 틀입니다.
    panel.innerHTML = `
        <h3 style="margin-top:0">예약 정보</h3>

        <p id="bookingMember" class="muted"></p>

        <label for="bookingSeats">탑승 인원</label>
        <input id="bookingSeats" type="number" min="1" max="4"
               value="1" required
               style="width:100%;height:44px;padding:10px;
                      border:1px solid #d6deea;border-radius:8px;
                      font:inherit;margin-bottom:16px">

        <label for="bookingCoupon">사용할 쿠폰</label>
        <select id="bookingCoupon">
            <option value="">쿠폰 사용 안 함</option>
        </select>

        <p class="muted" style="margin-top:14px">
            예상 합계
        </p>
        <p id="bookingTotal"
           style="font-size:26px;font-weight:800;margin:6px 0">
            —
        </p>
        <p class="muted">
            최종 금액과 쿠폰 사용 가능 여부는 서버가 확인합니다.
        </p>

        <button id="bookingSubmit" type="button">
            예약 요청
        </button>

        <p id="bookingFeedback" role="status"
           aria-live="polite" style="line-height:1.7"></p>

        <form id="captchaForm" hidden
              style="grid-template-columns:1fr;gap:12px">
            <label id="captchaQuestion" for="captchaAnswer">
                추가 확인
            </label>

            <input id="captchaAnswer" required
                   inputmode="numeric" autocomplete="off"
                   placeholder="정답 입력"
                   style="width:100%;height:44px;padding:10px;
                          border:1px solid #d6deea;border-radius:8px;
                          font:inherit">

            <button id="captchaSubmit" type="submit">
                답 제출
            </button>

            <p class="muted">
                실험용 추가 확인 절차입니다.
                확인이 끝나기 전에는 좌석이 확보되지 않습니다.
            </p>
        </form>
    `;

    // 상세 내용과 닫기 버튼 사이에 예약 화면을 넣습니다.
    content.after(panel);

    const seatsInput = document.getElementById("bookingSeats");
    const couponSelect = document.getElementById("bookingCoupon");
    const submit = document.getElementById("bookingSubmit");
    const feedback = document.getElementById("bookingFeedback");
    const captchaForm = document.getElementById("captchaForm");
    const captchaSubmit = document.getElementById("captchaSubmit");
    const closeButton = document.getElementById("closeDialog");

    function setFeedback(text, error = false) {
        feedback.textContent = text;
        feedback.style.color = error ? "#b42318" : "#234873";
    }

    function showCaptcha(visible) {
        captchaForm.hidden = !visible;
        captchaForm.style.display = visible ? "grid" : "none";
    }

    function setBusy(value) {
        busy = value;
        submit.disabled = value || reservationId !== null;
        seatsInput.disabled = value || reservationId !== null;
        couponSelect.disabled = value || reservationId !== null;
        captchaSubmit.disabled = value;
        closeButton.disabled = value;
    }

    // 処理中に閉じて結果を見失わないようにします。
    dialog.addEventListener("cancel", event => {
        if (busy) event.preventDefault();
    });

    async function api(path, options = {}) {
        const response = await fetch(path, {
            cache: "no-store",
            ...options
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(
                typeof data.detail === "string"
                    ? data.detail
                    : "予約内容を確認して再度お試しください。"
            );
        }

        return data;
    }

    function updateTotal() {
        if (!currentFlight) return;

        const seats = Number(seatsInput.value);
        const coupon = coupons.find(
            item => String(item.id) === couponSelect.value
        );

        if (!Number.isInteger(seats) || seats < 1 || seats > 4) {
            document.getElementById("bookingTotal").textContent =
                "1〜4名で選択してください";
            return;
        }

        const total = Math.max(
            currentFlight.price * seats -
            (coupon ? coupon.discount_amount : 0),
            0
        );

        document.getElementById("bookingTotal").textContent =
            money(total);
    }

    function applyResult(result) {
        reservationId = result.reservation.id;

        document.getElementById("bookingTotal").textContent =
            money(result.reservation.total_price);

        showCaptcha(false);

        if (result.decision === "CONFIRMED") {
            setFeedback(
                `예약 완료! 예약번호는 ${reservationId}입니다. ` +
                `확정 금액은 ${money(result.reservation.total_price)}입니다.`
            );
            submit.textContent = "예약 완료";

        } else if (result.decision === "CAPTCHA_REQUIRED") {
            setFeedback(
                `예약번호 ${reservationId}: 추가 확인이 필요합니다. ` +
                "아래 문제에 답해 주세요."
            );

            document.getElementById("captchaQuestion").textContent =
                result.captcha_question || "추가 확인 문제";

            document.getElementById("captchaAnswer").value = "";
            submit.textContent = "추가 확인 대기";
            showCaptcha(true);

        } else if (result.decision === "BLOCKED") {
            setFeedback(
                "현재 요청은 예약 정책에 따라 제한되었습니다. " +
                "좌석과 쿠폰은 사용되지 않았습니다.",
                true
            );
            submit.textContent = "예약 제한";

        } else {
            setFeedback(
                `예약 상태: ${result.decision}. 예약 내역을 확인해 주세요.`
            );
        }

        // 기존 검색 화면의 잔여 좌석을 갱신합니다.
        // 갱신 실패를 예약 실패로 표시하지 않습니다.
        if (typeof searchFlights === "function") {
            searchFlights().catch(() => {});
        }
    }

    // index.html의 상세 조회 함수에서 호출할 연결 함수
    window.prepareBooking = async function (flight) {
        const version = ++loadVersion;

        currentFlight = flight;
        coupons = [];
        reservationId = null;

        seatsInput.value = "1";
        couponSelect.replaceChildren(
            new Option("쿠폰 사용 안 함", "")
        );

        submit.textContent = "예약 요청";
        showCaptcha(false);
        setFeedback("");
        setBusy(true);
        updateTotal();

        const userId = sessionStorage.getItem("alarmFlightUserId");

        if (!userId) {
            document.getElementById("bookingMember").textContent =
                "예약하려면 상단의 회원가입을 먼저 진행해 주세요.";

            busy = false;
            closeButton.disabled = false;
            submit.disabled = true;
            return;
        }

        document.getElementById("bookingMember").textContent =
            `실험용 회원번호 ${userId}로 예약합니다.`;

        try {
            const data = await api(`/users/${userId}/coupons`);

            if (version !== loadVersion) return;

            // 사용 완료·만료 쿠폰은 목록에서 제외합니다.
            // 최종 검증은 서버에서 다시 수행합니다.
            coupons = data.filter(coupon =>
                !coupon.used_at &&
                (
                    !coupon.expires_at ||
                    new Date(coupon.expires_at).getTime() > Date.now()
                )
            );

            for (const coupon of coupons) {
                couponSelect.add(new Option(
                    `${money(coupon.discount_amount)} 할인`,
                    String(coupon.id)
                ));
            }

            setBusy(false);
            updateTotal();

        } catch (error) {
            if (version !== loadVersion) return;

            setBusy(false);
            setFeedback(
                `쿠폰 조회 실패: ${error.message} 쿠폰 없이 예약할 수 있습니다.`,
                true
            );
        }
    };

    seatsInput.addEventListener("input", updateTotal);
    couponSelect.addEventListener("change", updateTotal);

    submit.addEventListener("click", async () => {
        if (busy || reservationId !== null || !currentFlight) return;

        const userId = Number(
            sessionStorage.getItem("alarmFlightUserId")
        );

        const seats = Number(seatsInput.value);

        if (!userId) {
            setFeedback("회원가입을 먼저 진행해 주세요.", true);
            return;
        }

        if (!Number.isInteger(seats) || seats < 1 || seats > 4) {
            setFeedback("탑승 인원은 1~4명으로 입력해 주세요.", true);
            return;
        }

        if (!window.confirm(
            `${currentFlight.flight_no} 항공편 ${seats}명 예약을 요청할까요?`
        )) {
            return;
        }

        setBusy(true);
        setFeedback("예약 요청을 처리하고 있습니다.");

        try {
            const result = await api("/reservations", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    user_id: userId,
                    flight_id: currentFlight.id,
                    seat_count: seats,
                    coupon_id: couponSelect.value
                        ? Number(couponSelect.value)
                        : null,
                    device_id: localStorage.getItem("alarmFlightDeviceId")
                })
            });

            applyResult(result);

        } catch (error) {
            setFeedback(
                `요청 처리 확인 필요: ${error.message} ` +
                "통신이 끊겼다면 서버에서 예약됐을 수도 있으니 반복 요청하지 말고 예약 내역을 확인해 주세요.",
                true
            );
        } finally {
            setBusy(false);
        }
    });

    captchaForm.addEventListener("submit", async event => {
        event.preventDefault();
        if (busy || reservationId === null) return;

        setBusy(true);
        setFeedback("추가 확인 답안을 제출하고 있습니다.");

        try {
            const result = await api(
                `/reservations/${reservationId}/captcha`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        answer: document.getElementById(
                            "captchaAnswer"
                        ).value.trim()
                    })
                }
            );

            applyResult(result);

        } catch (error) {
            setFeedback(error.message, true);
        } finally {
            setBusy(false);
        }
    });

    showCaptcha(false);
})();