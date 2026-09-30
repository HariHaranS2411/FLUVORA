/* Service worker: displays flood notifications even when the site is closed.
   Receives Web Push messages from the flood backend and opens the relevant
   district/alert page when the notification is clicked. */

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: "Flood alert", body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "Flood alert";
  const options = {
    body: data.body || "",
    icon: "/favicon.svg",
    badge: "/favicon.svg",
    tag: data.district_id ? `flood-${data.district_id}` : "flood-alert",
    renotify: true,
    data: { url: data.url || "/alerts" },
    requireInteraction: data.district_id ? false : true,
    vibrate: [200, 100, 200],
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/alerts";
  const target = new URL(url, self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if (client.url === target && "focus" in client) return client.focus();
        // if any app tab is open, navigate it
        if ("focus" in client && client.url.startsWith(self.location.origin)) {
          return client.navigate(target).then((c) => (c ? c : client.focus()));
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
