import { useEffect, useState } from "react";
function useRoute() {
  const [route, setRoute] = useState(
    () => location.hash.startsWith("#/") ? location.hash.slice(2) || "inbox" : "inbox",
  );
  useEffect(() => {
    const f = () => { if (location.hash.startsWith("#/")) setRoute(location.hash.slice(2) || "inbox"); };
    addEventListener("hashchange", f);
    return () => removeEventListener("hashchange", f);
  }, []);
  return route;
}
export { useRoute };
