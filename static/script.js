document.addEventListener("DOMContentLoaded", async () => {
    // 必須要素の参照
    const video = document.getElementById("camera");
    const canvas = document.getElementById("photoCanvas");
    const fileInput = document.getElementById("fileInput");
    const captureButton = document.querySelector(".custom-file-upload"); 
    // プレビュー要素を追加
    const previewImage = document.getElementById("photoPreview"); 

    // HTML要素の参照が失敗した場合に処理を中断
    // プレビュー要素を追加
    if (!video || !canvas || !fileInput || !captureButton || !previewImage) { 
        console.error("🔴 必須のHTML要素が見つかりません。カメラ関連機能は動作しません。");
        return;
    }

    let isCameraReady = false;
    let cameraStream = null; // ストリームを停止できるように変数化

    try {
        // 1. カメラ起動 (HTTPS接続とユーザー許可が必須)
        const stream = await navigator.mediaDevices.getUserMedia({
            video: { 
                facingMode: "environment" // 背面カメラを優先
            }
        });
        cameraStream = stream; // ストリームを保存
        video.srcObject = stream;
        
        // 2. ストリームの準備完了を待つ
        video.onloadedmetadata = () => {
            console.log("カメラストリームの準備ができました。");
            isCameraReady = true;
            captureButton.textContent = "📸 撮影する"; 
        };

    } catch (err) {
        console.error("🔴 カメラ起動エラー:", err);
        alert("カメラにアクセスできません。権限を確認するか、サイトがHTTPS接続になっているか確認してください。");
        captureButton.textContent = "ファイルを選択"; // カメラが使えない場合はファイル選択を促す
        captureButton.addEventListener("click", () => {
             fileInput.click(); // カメラが使えない場合はinput[type=file]を直接開く
        });
        return;
    }

    // 📸 撮影ボタンクリック時の処理
    captureButton.addEventListener("click", (event) => {
        event.preventDefault(); 

        if (!isCameraReady || !video.srcObject) {
            alert("カメラがまだ準備できていません。しばらく待ってから再度お試しください。");
            return;
        }

        const context = canvas.getContext("2d");
        
        // 映像のサイズに合わせてCanvasを設定
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        
        // 📷 プレビュー表示のための処理 
        const dataURL = canvas.toDataURL("image/jpeg");
        previewImage.src = dataURL; // プレビュー画像を設定
        previewImage.style.display = 'block'; // プレビューを表示
        video.style.display = 'none'; // カメラ映像を非表示

        // 💡 カメラストリームを停止し、ライトを消す (省略可能だが推奨)
        if (cameraStream) {
            cameraStream.getTracks().forEach(track => track.stop());
            video.srcObject = null;
            isCameraReady = false;
        }


        // Canvas を Blob に変換して input[type=file] にセット
        canvas.toBlob((blob) => {
            if (!blob) {
                alert("キャプチャに失敗しました。");
                return;
            }
            
            // ファイルオブジェクトの作成
            const file = new File([blob], "capture_" + Date.now() + ".jpeg", { type: "image/jpeg" });
            
            // DataTransferを使用してinput[type=file]に値をセット
            const dataTransfer = new DataTransfer();
            dataTransfer.items.add(file);
            fileInput.files = dataTransfer.files;
            
            alert("✅ 写真を撮影しました！フォームにセットされました。");
            captureButton.textContent = "📸 撮影完了 (再撮影)"; // ボタンテキストを更新
            isCameraReady = true; // 再撮影のためにフラグを一時的に戻す
            
        }, "image/jpeg", 0.9); // JPEG形式、品質0.9
    });
});

// ファイル選択時（カメラが使えなかった場合など）のプレビュー機能
document.addEventListener("change", (event) => {
    const fileInput = document.getElementById("fileInput");
    const previewImage = document.getElementById("photoPreview");
    const video = document.getElementById("camera");

    if (event.target === fileInput && fileInput.files && fileInput.files[0]) {
        const reader = new FileReader();
        reader.onload = (e) => {
            previewImage.src = e.target.result;
            previewImage.style.display = 'block';
            video.style.display = 'none'; // カメラが使えない場合の対応
        };
        reader.readAsDataURL(fileInput.files[0]);
    }
});