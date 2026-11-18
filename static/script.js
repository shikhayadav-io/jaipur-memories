(() => {
  "use strict";

  const elements = {
    loginView: document.querySelector("#login-view"),
    galleryView: document.querySelector("#gallery-view"),
    loginForm: document.querySelector("#login-form"),
    loginMessage: document.querySelector("#login-message"),
    loginSubmit: document.querySelector("#login-submit"),
    headerActions: document.querySelector("#header-actions"),
    userName: document.querySelector("#user-name"),
    uploadButton: document.querySelector("#upload-button"),
    logoutButton: document.querySelector("#logout-button"),
    photoInput: document.querySelector("#photo-input"),
    photoGrid: document.querySelector("#photo-grid"),
    photoCount: document.querySelector("#photo-count"),
    galleryMessage: document.querySelector("#gallery-message"),
    lightbox: document.querySelector("#lightbox"),
    lightboxImage: document.querySelector("#lightbox-image"),
    lightboxCaption: document.querySelector("#lightbox-caption"),
    lightboxDownload: document.querySelector("#lightbox-download"),
    lightboxClose: document.querySelector("#lightbox-close"),
    toast: document.querySelector("#toast"),
  };
  let toastTimer;

  async function request(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
    });
    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      try {
        const body = await response.json();
        if (typeof body.detail === "string") message = body.detail;
      } catch {
        // Keep the HTTP status message when the server does not return JSON.
      }
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  function showToast(message, isError = false) {
    window.clearTimeout(toastTimer);
    elements.toast.textContent = message;
    elements.toast.classList.toggle("is-error", isError);
    elements.toast.classList.add("is-visible");
    toastTimer = window.setTimeout(() => elements.toast.classList.remove("is-visible"), 3200);
  }

  function showLoggedOut() {
    elements.headerActions.hidden = true;
    elements.galleryView.hidden = true;
    elements.loginView.hidden = false;
    elements.photoGrid.replaceChildren();
    elements.photoCount.textContent = "";
    elements.galleryMessage.textContent = "";
  }

  function showLoggedIn(user) {
    elements.userName.textContent = user.username;
    elements.uploadButton.hidden = !["admin", "uploader"].includes(user.role);
    elements.loginView.hidden = true;
    elements.galleryView.hidden = false;
    elements.headerActions.hidden = false;
    loadPhotos();
  }

  async function loadPhotos() {
    elements.photoGrid.replaceChildren();
    elements.galleryMessage.textContent = "Gathering your Jaipur memories…";
    elements.photoCount.textContent = "";
    try {
      const photos = await request("/photos");
      elements.photoCount.textContent = `${photos.length} ${photos.length === 1 ? "memory" : "memories"}`;
      elements.galleryMessage.textContent = "";
      if (photos.length === 0) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = "No photos just yet. This is where our Jaipur story begins.";
        elements.photoGrid.append(empty);
        return;
      }

      const fragment = document.createDocumentFragment();
      for (const photo of photos) {
        const card = document.createElement("button");
        card.className = "photo-card";
        card.type = "button";
        card.setAttribute("aria-label", `Preview ${photo.filename}`);

        const image = document.createElement("img");
        image.src = photo.url;
        image.alt = photo.filename;
        image.loading = "lazy";
        image.decoding = "async";

        const name = document.createElement("span");
        name.className = "photo-name";
        name.textContent = photo.filename;
        card.append(image, name);
        card.addEventListener("click", () => openLightbox(photo));
        fragment.append(card);
      }
      elements.photoGrid.append(fragment);
    } catch (error) {
      if (error.status === 401) {
        showLoggedOut();
        elements.loginMessage.textContent = "Your session has expired. Please sign in again.";
        return;
      }
      elements.galleryMessage.textContent = error.message || "We couldn't load the photos. Please try again.";
    }
  }

  function openLightbox(photo) {
    elements.lightboxImage.src = photo.url;
    elements.lightboxImage.alt = photo.filename;
    elements.lightboxCaption.textContent = photo.filename;
    elements.lightboxDownload.href = photo.url;
    elements.lightboxDownload.download = photo.filename;
    elements.lightbox.showModal();
  }

  elements.loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    elements.loginMessage.textContent = "";
    elements.loginSubmit.disabled = true;
    elements.loginSubmit.querySelector("span").textContent = "Opening album…";

    const formData = new FormData(elements.loginForm);
    try {
      const user = await request("/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: formData.get("username"),
          password: formData.get("password"),
        }),
      });
      elements.loginForm.reset();
      showLoggedIn(user);
    } catch (error) {
      elements.loginMessage.textContent = error.message || "Sign in didn't work. Please try again.";
    } finally {
      elements.loginSubmit.disabled = false;
      elements.loginSubmit.querySelector("span").textContent = "Open the album";
    }
  });

  elements.uploadButton.addEventListener("click", () => elements.photoInput.click());
  elements.photoInput.addEventListener("change", async () => {
    const [file] = elements.photoInput.files;
    if (!file) return;

    if (file.size > 15 * 1024 * 1024) {
      showToast("That image is larger than 15 MB.", true);
      elements.photoInput.value = "";
      return;
    }

    const body = new FormData();
    body.append("file", file);
    elements.uploadButton.disabled = true;
    try {
      await request("/upload", { method: "POST", body });
      showToast("Your photo has been added to the album.");
      await loadPhotos();
    } catch (error) {
      if (error.status === 401) {
        showLoggedOut();
        elements.loginMessage.textContent = "Your session has expired. Please sign in again.";
      } else {
        showToast(error.message || "We couldn't upload that photo.", true);
      }
    } finally {
      elements.uploadButton.disabled = false;
      elements.photoInput.value = "";
    }
  });

  elements.logoutButton.addEventListener("click", async () => {
    try {
      await request("/logout", { method: "POST" });
      showLoggedOut();
    } catch (error) {
      showToast(error.message || "Couldn't log out. Please try again.", true);
    }
  });

  elements.lightboxClose.addEventListener("click", () => elements.lightbox.close());
  elements.lightbox.addEventListener("click", (event) => {
    if (event.target === elements.lightbox) elements.lightbox.close();
  });
  elements.lightbox.addEventListener("close", () => {
    elements.lightboxImage.removeAttribute("src");
    elements.lightboxDownload.removeAttribute("href");
  });

  request("/me")
    .then(showLoggedIn)
    .catch((error) => {
      showLoggedOut();
      if (error.status !== 401) {
        elements.loginMessage.textContent = "Couldn't check your session. Please sign in.";
      }
    });
})();
