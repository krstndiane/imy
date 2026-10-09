/* Icon: small line icons for the chat skins (header and message box). Add one by adding a path. */
const PATHS = {
  back: <path d="M15 5l-7 7 7 7" />,
  phone: <path d="M6.5 3.5h3l1.5 4-2 1.5a12 12 0 0 0 6 6l1.5-2 4 1.5v3a2 2 0 0 1-2.2 2A16.5 16.5 0 0 1 4.5 5.700a2 2 0 0 1 2-2.200z" />,
  video: <><rect x="3" y="6.500" width="12" height="11" rx="2.500" /><path d="M15 10.500l5.500-3v9l-5.500-3z" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5.500M12 7.600v.1" /></>,
  more: <><circle cx="12" cy="5" r="1.200" /><circle cx="12" cy="12" r="1.200" /><circle cx="12" cy="19" r="1.200" /></>,
  plus: <><circle cx="12" cy="12" r="9" /><path d="M12 8v8M8 12h8" /></>,
  camera: <><path d="M4 8.500h3l1.500-2.500h7L17 8.500h3a1 1 0 0 1 1 1V18a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9.500a1 1 0 0 1 1-1z" /><circle cx="12" cy="13.500" r="3.200" /></>,
  image: <><rect x="3.500" y="4.500" width="17" height="15" rx="2.500" /><circle cx="9" cy="10" r="1.500" /><path d="M4 17l5-4.500 3.500 3 3-2.500 4.500 4" /></>,
  mic: <><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.500 11.500a6.500 6.500 0 0 0 13 0M12 18v3" /></>,
  smile: <><circle cx="12" cy="12" r="9" /><path d="M8.500 14.500a4.500 4.500 0 0 0 7 0M9 9.800v.1M15 9.800v.1" /></>,
  clip: <path d="M19.500 11.500l-7.800 7.800a4.600 4.600 0 0 1-6.500-6.500l8.300-8.300a3.100 3.100 0 0 1 4.400 4.400l-8.200 8.200a1.600 1.600 0 0 1-2.300-2.300l7.300-7.300" />,
  like: <path d="M7 11v9H4v-9zM7 11l4-7.500a2 2 0 0 1 2.500 2L13 9.500h5.500a2 2 0 0 1 2 2.400l-1.300 6.500a2 2 0 0 1-2 1.600H7" />,
  send: <path d="M4 12L20.500 4l-4 16-4.500-6.500zM12 13.500L20.500 4" />,
  up: <path d="M12 19V6M6.500 11.500L12 6l5.500 5.500" />,
  reply: <path d="M9 7L4 12l5 5M4 12h9a7 7 0 0 1 7 7v1" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="M15.8 15.8l4.7 4.7" /></>,
  file: <><path d="M13 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9.5z" /><path d="M13 3.5v6h6" /></>,
  play: <><circle cx="12" cy="12" r="9" /><path d="M10 8.5l6 3.5-6 3.5z" /></>,
  trash: <path d="M5 7h14M10 7V5h4v2M6.5 7l.8 12a2 2 0 0 0 2 1.9h5.4a2 2 0 0 0 2-1.9l.8-12" />,
};

export default function Icon({ name }) {
  return (
    <svg className="icon-svg" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}
