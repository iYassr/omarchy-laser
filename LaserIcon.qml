import QtQuick

// A reticle: the thing a laser is pointed at. Vector, rasterized at the requested size.
Image {
  id: root
  property color ink: "#cacccc"
  property bool hot: false  // draws the beam dot solid and larger while locked on
  width: 16
  height: 16
  sourceSize.width: Math.ceil(width * 2)
  sourceSize.height: Math.ceil(height * 2)
  smooth: true
  source: "data:image/svg+xml," + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="' + ink + '" stroke-width="2" stroke-linecap="round">'
    + '<circle cx="12" cy="12" r="7.5"/>'
    + '<path d="M12 1.5v4M12 18.5v4M1.5 12h4M18.5 12h4"/>'
    + '<circle cx="12" cy="12" r="' + (hot ? 3 : 2.2) + '" fill="' + ink + '" stroke="none"/></svg>')
}
