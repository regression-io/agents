import os
import json
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional

from .base import BaseRetriever
from ..storages import BaseVectorStorage, VectorDBQuery, VectorRecord
from ...utils.embeddings import BaseEmbedding, OpenAIEmbedding
from ...utils.unstructured_io import UnstructuredIO

DEFAULT_TOP_K_RESULTS = 1
DEFAULT_SIMILARITY_THRESHOLD = 0.75


class VectorRetriever(BaseRetriever):
    r"""An implementation of the `BaseRetriever` by using vector storage and
    embedding model.

    This class facilitates the retriever of relevant information using a
    query-based approach, backed by vector embeddings.

    Attributes:
        embedding_model (BaseEmbedding): Embedding model used to generate
            vector embeddings.
    """

    def __init__(self, embedding_model: Optional[BaseEmbedding] = None) -> None:
        r"""Initializes the retriever class with an optional embedding model.

        Args:
            embedding_model (Optional[BaseEmbedding]): The embedding model
                instance. Defaults to `OpenAIEmbedding` if not provided.
        """
        self.embedding_model = embedding_model or OpenAIEmbedding()

    def process(  # type: ignore
        self, content_input_path: str, storage: BaseVectorStorage, **kwargs: Any
    ) -> None:
        r"""Processes content from a file or URL, divides it into chunks by
        using `Unstructured IO`, and stores their embeddings in the specified
        vector storage.

        Args:
            content_input_path (str): File path or URL of the content to be
                processed.
            storage (BaseVectorStorage): Vector storage to store the
                embeddings.
            **kwargs (Any): Additional keyword arguments for elements chunking.
        """

        # Check if the input is a URL
        parsed_url = urlparse(content_input_path)
        is_url = all([parsed_url.scheme, parsed_url.netloc])

        if not is_url:
            # Determine the file format based on its extension
            _, file_extension = os.path.splitext(content_input_path)

            if file_extension.lower() == ".jsonl" or file_extension.lower() == ".json":
                try:
                    with open(content_input_path, "r", encoding="utf-8") as file:
                        # Handle JSONL: read each line as a separate JSON object
                        if file_extension.lower() == ".jsonl":
                            data = [
                                json.loads(line.strip())
                                for line in file
                                if line.strip()
                            ]
                        # Handle JSON: read the entire file as a single JSON object
                        elif file_extension.lower() == ".json":
                            data = json.load(file)
                except json.JSONDecodeError as e:
                    raise Exception(
                        f"Error decoding JSON from {content_input_path}: {e}"
                    )

            if isinstance(data, list):
                chunks = data
            else:
                raise ValueError(
                    f"Invalid data format in {content_input_path}. Expected a list."
                )

        else:
            unstructured_modules = UnstructuredIO()
            elements = unstructured_modules.parse_file_or_url(content_input_path)
            chunks = unstructured_modules.chunk_elements(
                chunk_type="chunk_by_title", elements=elements, **kwargs
            )

        # Iterate to process and store embeddings, set batch of 50
        for i in range(0, len(chunks), 50):
            batch_chunks = chunks[i : i + 50]
            batch_vectors = self.embedding_model.embed_list(
                objs=[str(chunk) for chunk in batch_chunks]
            )

            records = []
            # Prepare the payload for each vector record, includes the content
            # path, chunk metadata, and chunk text
            for vector, chunk in zip(batch_vectors, batch_chunks):
                content_path_info = {"content path": content_input_path}
                if hasattr(chunk, "metadata"):
                    chunk_metadata = {"metadata": chunk.metadata.to_dict()}
                else:
                    chunk_metadata = {"metadata": {}}
                chunk_text = {"text": str(chunk)}
                combined_dict = {**content_path_info, **chunk_metadata, **chunk_text}

                records.append(VectorRecord(vector=vector, payload=combined_dict))

            storage.add(records=records)

    def query(  # type: ignore
        self,
        query: str,
        storage: BaseVectorStorage,
        top_k: int = DEFAULT_TOP_K_RESULTS,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        r"""Executes a query in vector storage and compiles the retrieved
        results into a dictionary.

        Args:
            query (str): Query string for information retriever.
            storage (BaseVectorStorage): Vector storage to query.
            top_k (int, optional): The number of top results to return during
                retriever. Must be a positive integer. Defaults to 1.
            similarity_threshold (float, optional): The similarity threshold
                for filtering results. Defaults to 0.75.
            **kwargs (Any): Additional keyword arguments for vector storage
                query.

        Returns:
            List[Dict[str, Any]]: Concatenated list of the query results.

        Raises:
            ValueError: If 'top_k' is less than or equal to 0, if vector
                storage is empty, if payload of vector storage is None.
        """

        if top_k <= 0:
            raise ValueError("top_k must be a positive integer.")

        # Load the storage incase it's hosted remote
        storage.load()

        query_vector = self.embedding_model.embed(obj=query)
        db_query = VectorDBQuery(query_vector=query_vector, top_k=top_k)
        query_results = storage.query(query=db_query, **kwargs)

        if query_results[0].record.payload is None:
            raise ValueError(
                "Payload of vector storage is None, please check" " the collection."
            )

        # format the results
        formatted_results = []
        for result in query_results:
            if (
                result.similarity >= similarity_threshold
                and result.record.payload is not None
            ):
                result_dict = {
                    "similarity score": str(result.similarity),
                    "content path": result.record.payload.get("content path", ""),
                    "metadata": result.record.payload.get("metadata", {}),
                    "text": result.record.payload.get("text", ""),
                }
                formatted_results.append(result_dict)

        content_path = query_results[0].record.payload.get("content path", "")

        if not formatted_results:
            return [
                {
                    "text": f"""No suitable information retrieved from {content_path} \
                with similarity_threshold = {similarity_threshold}."""
                }
            ]
        return formatted_results
