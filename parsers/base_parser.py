from abc import ABC, abstractmethod

class BaseParser(ABC):
    @abstractmethod
    def parse(self, content: str):
        """
        Parses the file content into code elements.

        Returns:
            A list of dictionaries, where each dictionary represents a code element.
            Each dict should have keys: 'kind', 'name', 'content', 'start_line', 'end_line'.
        """
        pass 