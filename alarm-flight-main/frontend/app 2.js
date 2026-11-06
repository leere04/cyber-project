(() => {
    const USER_KEY = "alarmFlightUserId";
    const DEVICE_KEY = "alarmFlightDeviceId";

    // 같은 브라우저에서는 같은 실험용 기기 ID를 사용합니다.
    // 이 값은 실제 기기 신원을 증명하는 인증 정보는 아닙니다.
    let deviceId = localStorage.getItem(DEVICE_KEY);

    if (!deviceId) {
        deviceId = crypto.randomUUID();
        localStorage.setItem(DEVICE_KEY, deviceId);
    }

    // 화면 위쪽에 회원가입 버튼 추가
    const memberArea = document.createElement("div");
    memberArea.style.cssText =
        "display:flex;gap:12px;align-items:center;flex-wrap:wrap";

    const memberLabel = document.createElement("span");
    memberLabel.className = "muted";
    memberLabel.textContent = "가입 전";

    const memberButton = document.createElement("button");
    memberButton.type = "button";
    memberButton.className = "secondary";
    memberButton.textContent = "회원가입";

    memberArea.append(memberLabel, memberButton);
    document.querySelector(".nav").append(memberArea);
    document.querySelector(".nav").style.flexWrap = "wrap";
    document.querySelector(".nav").style.gap = "16px";

    // 아래 HTML은 고정된 화면 틀입니다.
    const memberDialog = document.createElement("dialog");
    memberDialog.setAttribute("aria-labelledby", "memberTitle");

    memberDialog.innerHTML = `
        <h2 id="memberTitle">회원가입</h2>

        <p class="muted">
            모의 예약에 사용할 실험용 회원을 만듭니다.
            실제 서비스에서 사용하는 비밀번호는 입력하지 마세요.
        </p>

        <form id="memberForm"
              style="display:grid;grid-template-columns:1fr;gap:14px">
            <div>
                <label for="memberName">이름</label>
                <input id="memberName" required maxlength="40"
                       placeholder="예: 테스트 여행자">
            </div>

            <div>
                <label for="memberEmail">이메일</label>
                <input id="memberEmail" type="email" required
                       placeholder="예: traveler01@example.com">
            </div>

            <div>
                <label for="memberPassword">실험용 비밀번호</label>
                <input id="memberPassword" type="password" required
                       minlength="8" autocomplete="new-password"
                       placeholder="8자 이상 입력">
            </div>

            <button id="memberSubmit" type="submit">
                가입하고 쿠폰 받기
            </button>
        </form>

        <p id="memberFeedback" class="muted"
           role="status" aria-live="polite"></p>

        <div id="memberSummary" hidden></div>

        <button id="memberClose" type="button" class="secondary">
            닫기
        </button>
    `;

    document.body.append(memberDialog);

    // 회원가입 입력칸 스타일
    memberDialog.querySelectorAll("input").forEach(input => {
        input.style.cssText = `
            width:100%;
            height:46px;
            padding:0 12px;
            border:1px solid #d6deea;
            border-radius:8px;
            font:inherit;
            color:#182c49;
        `;
    });

    const form = document.getElementById("memberForm");
    const feedback = document.getElementById("memberFeedback");
    const summary = document.getElementById("memberSummary");
    const submit = document.getElementById("memberSubmit");

    let currentUser = null;

    function feedbackMessage(text, error = false) {
        feedback.textContent = text;
        feedback.style.color = error ? "#b42318" : "#475569";
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
                    : "입력 내용과 서버 상태를 확인해 주세요."
            );
        }

        return data;
    }

    function showMember(user) {
        currentUser = user;

        memberLabel.textContent = `실험용 회원: ${user.name}`;
        memberButton.textContent = "내 회원·쿠폰";
        document.getElementById("memberTitle").textContent =
            "실험용 회원 정보";

        form.hidden = true;
        // 기존 인라인 display:grid보다 hidden을 우선 적용
        form.style.display = "none";

        summary.hidden = false;
        summary.replaceChildren();

        const name = document.createElement("p");
        name.textContent = `${user.name} · 회원번호 ${user.id}`;

        const email = document.createElement("p");
        email.className = "muted";
        email.textContent = user.email;

        const note = document.createElement("p");
        note.className = "muted";
        note.textContent =
            "현재 탭에서 선택한 실험용 회원입니다. 로그인 인증 상태를 뜻하지 않습니다.";

        summary.append(name, email, note);
    }

    async function loadCoupons() {
        if (!currentUser) return;

        const coupons = await request(
            `/users/${currentUser.id}/coupons`
        );

        // 다시 열 때 쿠폰 목록이 중복되지 않게 제거합니다.
        summary.querySelector("[data-coupons]")?.remove();

        const section = document.createElement("section");
        section.dataset.coupons = "true";

        const title = document.createElement("h3");
        title.textContent = "보유 쿠폰";
        section.append(title);

        if (coupons.length === 0) {
            const empty = document.createElement("p");
            empty.textContent = "발급된 쿠폰이 없습니다.";
            section.append(empty);
        }

        for (const coupon of coupons) {
            const row = document.createElement("p");

            let status = "미사용";
            if (coupon.used_at) {
                status = "사용 완료";
            } else if (
                coupon.expires_at &&
                new Date(coupon.expires_at).getTime() < Date.now()
            ) {
                status = "만료";
            }

            row.textContent =
                `${Number(coupon.discount_amount).toLocaleString("ko-KR")}원 할인 · ${status}`;

            section.append(row);
        }

        summary.append(section);
    }

    memberButton.addEventListener("click", async () => {
        memberDialog.showModal();

        if (currentUser) {
            feedbackMessage("쿠폰을 확인하고 있습니다.");

            try {
                await loadCoupons();
                feedbackMessage(
                    "쿠폰의 최종 사용 가능 여부는 예약 시 서버가 확인합니다."
                );
            } catch (error) {
                feedbackMessage(`쿠폰 조회 실패: ${error.message}`, true);
            }
        }
    });

    document.getElementById("memberClose").addEventListener(
        "click",
        () => memberDialog.close()
    );

    form.addEventListener("submit", async event => {
        event.preventDefault();

        const name = document.getElementById("memberName").value.trim();
        const email = document.getElementById("memberEmail").value.trim();
        const passwordInput = document.getElementById("memberPassword");

        if (!name || !email) {
            feedbackMessage("이름과 이메일을 입력해 주세요.", true);
            return;
        }

        submit.disabled = true;
        feedbackMessage("회원가입을 처리하고 있습니다.");

        try {
            const data = await request("/users/signup", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    name,
                    email,
                    password: passwordInput.value,
                    device_id: deviceId
                })
            });

            // 비밀번호는 브라우저 저장소에 저장하지 않습니다.
            passwordInput.value = "";

            sessionStorage.setItem(USER_KEY, String(data.user.id));
            showMember(data.user);

            feedbackMessage(
                `회원가입 완료! ${Number(data.coupon.discount_amount).toLocaleString("ko-KR")}원 할인 쿠폰이 발급됐습니다.`
            );

            try {
                await loadCoupons();
            } catch (error) {
                feedbackMessage(
                    `가입은 완료됐지만 쿠폰 조회에 실패했습니다. ${error.message}`,
                    true
                );
            }

        } catch (error) {
            feedbackMessage(error.message, true);
        } finally {
            submit.disabled = false;
        }
    });

    async function restoreMember() {
        const savedId = sessionStorage.getItem(USER_KEY);
        if (!savedId) return;

        try {
            const user = await request(`/users/${savedId}`);
            showMember(user);
        } catch (error) {
            // 통신 장애를 회원 삭제로 단정하지 않습니다.
            memberLabel.textContent = "저장된 회원 확인 필요";
            memberButton.disabled = true;
            memberButton.textContent = "새로고침 후 확인";
            feedbackMessage(error.message, true);
        }
    }

    restoreMember();
})();