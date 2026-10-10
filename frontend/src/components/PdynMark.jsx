// The maker's mark, shown at the foot of every screen. The artwork is white, so the light theme flips it dark (styles.css).
export default function PdynMark({ height = 24 }) {
  return <img className="pdyn-mark" src="/media/pdyn-logo.png" alt="PDYN Technologies" height={height} style={{ height, width: "auto" }} />;
}
